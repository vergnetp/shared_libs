from __future__ import annotations

import re
import json
import time
from typing import Dict, Any, List, Optional
from pathlib import Path

from execute_cmd import CommandExecuter
from execute_docker import DockerExecuter
from logger import Logger
import env_loader
from resource_resolver import ResourceResolver

# Optional: precise zone parsing for Cloudflare zones
try:
    import tldextract as _tldextract
except Exception:
    _tldextract = None


def log(msg: str) -> None:
    Logger.log(msg)


def _registrable_zone(domain: str) -> str:
    """
    Return the registrable 'zone' for a domain (e.g., 'api.eu.example.co.uk' -> 'example.co.uk').
    Uses tldextract if available; falls back to a naive last-two-labels heuristic.
    """
    d = domain.lstrip("*.").strip()
    if not d:
        return d
    if _tldextract:
        ext = _tldextract.extract(d)  # subdomain, domain, suffix
        if ext.domain and ext.suffix:
            return f"{ext.domain}.{ext.suffix}"
        return d
    parts = d.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else d


class NginxConfigGenerator:
    """
    Nginx config generator for HTTP/HTTPS and TCP/stream (for internal service mesh).
    
    NEW: Stream support for TCP services (postgres, redis, etc.) to enable service discovery
    via localhost:INTERNAL_PORT regardless of backend deployment strategy (single vs multi-server).
    """

    # ---- Internal paths on the REMOTE host ----
    LE_ETC = "/etc/letsencrypt"
    LE_VAR = "/var/lib/letsencrypt"
    LE_LOG = "/var/log/letsencrypt"
    SSL_DIR = "/etc/nginx/ssl"
    MAIN_NGINX = "/etc/nginx/nginx.conf"
    CONFD_DIR = "/etc/nginx/conf.d"
    STREAMD_DIR = "/etc/nginx/stream.d"  # NEW: Stream configs directory

    # ---- Cloudflare DNS defaults (proxied ON) ----
    CF_PROXIED_DEFAULT = True
    CF_TTL_DEFAULT = 1

    # ---- Nginx container name (fixed convention) ----
    NGINX_CONTAINER = "nginx"

    # ---- sensible Nginx defaults ----
    DEFAULTS: Dict[str, Any] = {
        "load_balance_method": "least_conn",
        "client_max_body_size": "100M",
        "proxy_timeout": 300,
        "keepalive_timeout": 65,
        "worker_connections": 1024,

        "ssl_protocols": "TLSv1.2 TLSv1.3",
        "ssl_ciphers": "HIGH:!aNULL:!MD5",
        "ssl_session_cache": "shared:SSL:10m",
        "ssl_session_timeout": "10m",

        "health_check_interval": 10,
        "health_check_fails": 3,
        "health_check_passes": 2,

        "rate_limit_zone_size": "10m",
        "rate_limit": "100r/s",
        "rate_limit_burst": 20,

        "cache_static": True,
        "cache_static_expires": "30d",

        "gzip": True,
        "gzip_types": "text/plain application/json application/javascript text/css",

        "access_log": "/var/log/nginx/access.log",
        "error_log": "/var/log/nginx/error.log",
    }

    @staticmethod
    def _get_cert_paths(target_server: str) -> Dict[str, str]:
        """Get certificate paths for nginx - ALWAYS returns strings"""
        target_os = ResourceResolver.detect_target_os(target_server)
        
        if target_server == "localhost" or target_server is None:
            if target_os == "windows":
                base_str = "C:/local/nginx/certs"
            else:
                base_str = "/local/nginx/certs"
        else:
            # Remote servers: always Linux paths as strings
            base_str = "/local/nginx/certs"
        
        # Return string paths (no Path conversion)
        return {
            'etc': f"{base_str}/letsencrypt",
            'var': f"{base_str}/letsencrypt_var",
            'log': f"{base_str}/letsencrypt_log",
            'ssl': f"{base_str}/ssl"
        }

    @staticmethod
    def generate_stream_config(
        project: str,
        env: str,
        service_name: str,
        backends: List[Dict[str, Any]],
        listen_port: int,
        mode: str
    ) -> str:
        """
        Generate nginx stream configuration for TCP proxying (service mesh).
        
        Args:
            project: Project name
            env: Environment
            service_name: Service name
            backends: Backend list (format depends on mode)
                    single_server: [{"container_name": "...", "port": "5432"}, ...]
                    multi_server: [{"ip": "...", "port": 8357}, ...]
            listen_port: Internal port for nginx to listen on (stable)
            mode: "single_server" or "multi_server"
            
        Returns:
            Nginx stream config string
            
        Example (single-server):
            upstream myproj_dev_postgres {
                server myproj_dev_postgres:5432;
                server myproj_dev_postgres_secondary:5432;
                least_conn;
            }
            
            server {
                listen 5234;
                proxy_pass myproj_dev_postgres;
                proxy_connect_timeout 1s;
                proxy_timeout 10m;
            }
            
        Example (multi-server):
            upstream myproj_dev_api {
                server 10.0.0.1:8412;
                server 10.0.0.2:18412;
                least_conn;
            }
            
            server {
                listen 5890;
                proxy_pass myproj_dev_api;
                proxy_connect_timeout 1s;
                proxy_timeout 10m;
            }
        """
        upstream_name = f"{project}_{env}_{service_name}"
        
        # Build upstream block based on mode
        upstream = f"upstream {upstream_name} {{\n"
        
        if mode == "single_server":
            # Use container names (Docker DNS)
            for backend in backends:
                upstream += f"    server {backend['container_name']}:{backend['port']};\n"
        else:  # multi_server
            # Use IP + host ports
            for backend in backends:
                upstream += f"    server {backend['ip']}:{backend['port']};\n"
        
        upstream += "    least_conn;\n"
        upstream += "}\n\n"
        
        # Build server block (same for both modes)
        server = f"server {{\n"
        server += f"    listen {listen_port};\n"
        server += f"    proxy_pass {upstream_name};\n"
        server += f"    proxy_connect_timeout 1s;\n"
        server += f"    proxy_timeout 10m;\n"
        server += f"    proxy_next_upstream_timeout 5s;\n"
        server += f"    proxy_next_upstream_tries 2;\n"
        server += "}\n"
        
        return upstream + server

    @staticmethod
    def update_stream_config_on_server(
        server_ip: str,
        project: str,
        env: str,
        service: str,
        backends: List[Dict[str, Any]],
        internal_port: int,
        mode: str,
        user: str = "root"
    ) -> bool:
        """
        Write stream config file for a service and reload nginx.
        
        FIXED: Always uses string paths for remote servers to avoid Windows path conversion.
        """
        try:
                    
            # Generate stream config
            stream_config = NginxConfigGenerator.generate_stream_config(
                project, env, service, backends, internal_port, mode
            )
            
            # Detect target OS
            target_os = ResourceResolver.detect_target_os(server_ip, user)
            
            if server_ip == "localhost" or server_ip is None:
                # Local operation - can use Path objects
               
                
                if target_os == "windows":
                    stream_dir = Path("C:/local/nginx/stream.d")
                else:
                    stream_dir = Path("/etc/nginx/stream.d")
                
                stream_dir.mkdir(parents=True, exist_ok=True)
                config_file = stream_dir / f"{project}_{env}_{service}.conf"
                config_file.write_text(stream_config)
                
                log(f"Wrote stream config: {config_file}")
            else:
                # Remote operation - use STRING paths only (no Path conversion!)
                stream_dir = "/etc/nginx/stream.d"
                config_file = f"{stream_dir}/{project}_{env}_{service}.conf"
                
                # Write via SSH using string path
                write_cmd = f"cat > {config_file}"
                CommandExecuter.run_cmd_with_stdin(
                    write_cmd,
                    stream_config.encode('utf-8'),
                    server_ip,
                    user
                )
                
                log(f"Wrote stream config: {config_file}")
            
            # Reload nginx
            NginxConfigGenerator._reload_nginx(server_ip)
            
            return True
            
        except Exception as e:
            log(f"Failed to update stream config on {server_ip}: {e}")
            return False
    
    # =========================
    # INTERNALS
    # =========================

    @staticmethod
    def _get_main_nginx_path(target_server: str = "localhost") -> Path:
        """Get path to main nginx.conf file"""
      
        
        target_os = ResourceResolver.detect_target_os(target_server)
        
        if target_server == "localhost" or target_server is None:
            if target_os == "windows":
                return Path("C:/local/nginx/nginx.conf")
            else:
                return Path("/etc/nginx/nginx.conf")
        else:
            return Path(NginxConfigGenerator.MAIN_NGINX)

    @staticmethod
    def _detect_mode(email: Optional[str], cloudflare_api_token: Optional[str]) -> str:
        """Auto-detect cert issuance mode based on what credentials are provided"""
        if cloudflare_api_token:
            return "letsencrypt_dns_cloudflare"
        elif email:
            return "letsencrypt_standalone"
        else:
            return "selfsigned"

    # ----- Main nginx.conf management -----

    CF_BLOCK_BEGIN = "# BEGIN CF-REAL-IP (managed)"
    CF_BLOCK_END   = "# END CF-REAL-IP (managed)"

    @staticmethod
    def _ensure_main_nginx(target_server: str = "localhost") -> None:
        """
        Ensure nginx.conf exists with:
          - include /etc/nginx/conf.d/*.conf; (HTTP)
          - include /etc/nginx/stream.d/*.conf; (TCP/stream) [NEW]
          - a managed Cloudflare real_ip block
        """
        main = NginxConfigGenerator._get_main_nginx_path(target_server)
        http_include = "include /etc/nginx/conf.d/*.conf;"
        stream_include = "include /etc/nginx/stream.d/*.conf;"  # NEW

        if not main.exists():
            text = NginxConfigGenerator._generate_main_config(NginxConfigGenerator.DEFAULTS)
            main.parent.mkdir(parents=True, exist_ok=True)
            main.write_text(text)
            log(f"Created {main} with conf.d + stream.d includes and CF real_ip.")
            return

        # Read, ensure includes, and ensure CF block exists
        try:
            text = main.read_text()
        except Exception as e:
            log(f"Could not read {main}: {e}. Rewriting default.")
            text = NginxConfigGenerator._generate_main_config(NginxConfigGenerator.DEFAULTS)
            main.write_text(text)
            return

        updated = False
        
        # Ensure HTTP include
        if http_include not in text:
            text = text.replace("include /etc/nginx/mime.types;", 
                              f"include /etc/nginx/mime.types;\n    {http_include}")
            updated = True

        # Ensure CF block
        if NginxConfigGenerator.CF_BLOCK_BEGIN not in text or NginxConfigGenerator.CF_BLOCK_END not in text:
            cf_block = NginxConfigGenerator._render_cf_block(NginxConfigGenerator._default_cf_ranges())
            text = text.replace("default_type application/octet-stream;",
                              f"default_type application/octet-stream;\n\n    {cf_block}")
            updated = True

        if updated:
            main.write_text(text)
            log(f"Updated {main} to include conf.d, stream.d, and CF real_ip block.")

    @staticmethod
    def _render_cf_block(ranges: List[str]) -> str:
        lines = [NginxConfigGenerator.CF_BLOCK_BEGIN]
        for cidr in ranges:
            lines.append(f"set_real_ip_from {cidr};")
        lines.append("real_ip_header CF-Connecting-IP;")
        lines.append(NginxConfigGenerator.CF_BLOCK_END)
        return "\n    ".join(lines)

    @staticmethod
    def _default_cf_ranges() -> List[str]:
        return [
            "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
            "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
            "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
            "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
        ]

    @staticmethod
    def _refresh_cf_real_ip_in_main(target_server: str = "localhost") -> bool:
        """Fetch and update Cloudflare IP ranges in nginx.conf"""
        main = NginxConfigGenerator._get_main_nginx_path(target_server)
        if not main.exists():
            NginxConfigGenerator._ensure_main_nginx(target_server)
            return True

        try:
            old = main.read_text()
        except Exception as e:
            log(f"Cannot read {main}: {e}")
            return False

        v4 = NginxConfigGenerator._curl_local("https://www.cloudflare.com/ips-v4")
        v6 = NginxConfigGenerator._curl_local("https://www.cloudflare.com/ips-v6")
        if not v4 and not v6:
            log("Could not fetch Cloudflare IP ranges; keeping existing list.")
            return False

        ranges = []
        for txt in (v4 or "").splitlines():
            if txt.strip():
                ranges.append(txt.strip())
        for txt in (v6 or "").splitlines():
            if txt.strip():
                ranges.append(txt.strip())

        new_block = NginxConfigGenerator._render_cf_block(ranges or NginxConfigGenerator._default_cf_ranges())

        pattern = re.compile(
            re.escape(NginxConfigGenerator.CF_BLOCK_BEGIN) + r".*?" + re.escape(NginxConfigGenerator.CF_BLOCK_END),
            re.DOTALL,
        )
        if NginxConfigGenerator.CF_BLOCK_BEGIN in old and NginxConfigGenerator.CF_BLOCK_END in old:
            updated = pattern.sub(new_block, old)
        else:
            updated = old.replace("default_type application/octet-stream;",
                                f"default_type application/octet-stream;\n\n    {new_block}")

        if updated != old:
            main.write_text(updated)
            log("Updated Cloudflare real_ip ranges in nginx.conf.")
            return True

        log("Cloudflare real_ip ranges unchanged.")
        return False

    @staticmethod
    def _curl_local(url: str) -> Optional[str]:
        """Fetch URL from local machine"""
        try:
            out = CommandExecuter.run_cmd(
                f'powershell -Command "(Invoke-WebRequest -Uri {url} -UseBasicParsing).Content"',
                target_server=None
            )
            return str(out)
        except Exception:
            pass
        
        try:
            out = CommandExecuter.run_cmd(f'curl -fsSL "{url}"', target_server=None)
            return str(out)
        except Exception:
            return None

    # ----- Per-service HTTP conf + DNS + headers -----

    @staticmethod
    def _write_service_conf(
        project: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        *,
        target_server: Optional[str],
        cloudflare_api_token: Optional[str],
        auto_reload: bool = False,
    ) -> str:
        """
        Write nginx configuration for a service.
        
        Returns:
            Path to the written config file (as string)
        """
        nginx_cfg = NginxConfigGenerator._merge_with_defaults(service_config.get("nginx") or {})
        upstream_servers = NginxConfigGenerator._get_upstream_servers(project, env, service_name, service_config)
        conf_text = NginxConfigGenerator._generate_server_config(
            project, env, service_name, service_config, nginx_cfg, upstream_servers
        )

        conf_path_str = NginxConfigGenerator._get_conf_path(project, env, service_name, target_server)
        
        # Write config based on target
        if target_server == "localhost" or target_server is None:
            # Local: use Path for file operations
           
            conf_path = Path(conf_path_str)
            conf_path.parent.mkdir(parents=True, exist_ok=True)
            conf_path.write_text(conf_text)
            log(f"Wrote per-service config: {conf_path}")
        else:
            # Remote: use SSH to write file
                     
            # Ensure directory exists on remote
            conf_dir = str(Path(conf_path_str).parent)
            CommandExecuter.run_cmd(
                f"mkdir -p {conf_dir}",
                target_server,
                user="root"
            )
            
            # Write via SSH
            write_cmd = f"cat > {conf_path_str}"
            CommandExecuter.run_cmd_with_stdin(
                write_cmd,
                conf_text.encode('utf-8'),
                target_server,
                user="root"
            )
            log(f"Wrote per-service config: {conf_path_str}")

        # Handle DNS if needed
        domain = service_config.get("domain")
        if domain and cloudflare_api_token and not domain.startswith("*."):
            zone = _registrable_zone(domain)
            lb_ipv4 = NginxConfigGenerator._detect_public_ipv4(target_server)
            if lb_ipv4:
                try:
                    NginxConfigGenerator._cloudflare_upsert_records(
                        target_server or "localhost",
                        cf_token=cloudflare_api_token,
                        zone_name=zone,
                        records=[{"type": "A", "name": domain, "value": lb_ipv4}],
                        proxied=NginxConfigGenerator.CF_PROXIED_DEFAULT,
                        ttl=NginxConfigGenerator.CF_TTL_DEFAULT,
                    )
                except Exception as e:
                    log(f"Cloudflare DNS upsert failed (continuing): {e}")

        if auto_reload:
            NginxConfigGenerator._reload_nginx(target_server=target_server)

        return conf_path_str

    @staticmethod
    def _get_conf_path(project: str, env: str, service_name: str, target_server: str = "localhost") -> str:
        """
        Get the path to the nginx config file for a service.
        
        Returns:
            String path (works for both local and remote servers)
        """
            
        filename = ResourceResolver.get_nginx_config_name(project, env, service_name)
        
        if target_server == "localhost" or target_server is None:
            target_os = ResourceResolver.detect_target_os(None)
            if target_os == "windows":
                return f"C:/local/nginx/conf.d/{filename}"
            else:
                return f"/local/nginx/conf.d/{filename}"
        else:
            # Remote servers: always Linux paths
            return f"{NginxConfigGenerator.CONFD_DIR}/{filename}"

    @staticmethod
    def _collect_domains_for_service(service_config: Dict[str, Any]) -> List[str]:
        domains: List[str] = []
        primary = service_config.get("domain")
        if primary:
            domains.append(primary)
        alts = service_config.get("alt_names") or service_config.get("san") or []
        for x in alts:
            if x and x not in domains:
                domains.append(x)
        return domains

    # ----- Cloudflare DNS helpers -----

    @staticmethod
    def _detect_public_ipv4(target_server: Optional[str]) -> Optional[str]:
        if not target_server:
            return None
        try:
            val = str(
                CommandExecuter.run_cmd('curl -s ifconfig.me || curl -s ipinfo.io/ip', target_server)
            ).strip()
            if val and "html" not in val.lower():
                return val
        except Exception:
            pass
        return None

    @staticmethod
    def _ensure_main_nginx_local(target_os: str) -> None:
        """Create local nginx.conf file (for localhost only)"""
       
        
        if target_os == "windows":
            nginx_conf = Path("C:/local/nginx/nginx.conf")
        else:
            nginx_conf = Path("/etc/nginx/nginx.conf")
        
        nginx_conf.parent.mkdir(parents=True, exist_ok=True)
        
        content = """user nginx;
    worker_processes auto;
    error_log /var/log/nginx/error.log warn;
    pid /var/run/nginx.pid;

    events {
        worker_connections 1024;
    }

    stream {
        include /etc/nginx/stream.d/*.conf;
    }

    http {
        include /etc/nginx/mime.types;
        default_type application/octet-stream;
        sendfile on;
        keepalive_timeout 65;
        include /etc/nginx/conf.d/*.conf;
    }
    """
        nginx_conf.write_text(content)
        log(f"Created nginx.conf at {nginx_conf}")

    @staticmethod
    def ensure_nginx_container(
        project: str,
        env: str,
        services: Dict[str, Dict[str, Any]], 
        target_server: str = "localhost",
        user: str = "root"
    ) -> bool:
        """Ensure nginx container is running with proper configuration"""
     
        container_name = NginxConfigGenerator.NGINX_CONTAINER
        network_name = ResourceResolver.get_network_name(project, env)
        
        # Check if container exists
        check_cmd = "docker ps -a --filter 'name=^nginx$' --format '{{.Names}}'"
        result = CommandExecuter.run_cmd(check_cmd, target_server, user)
        
        if result and 'nginx' in str(result):
            # Container exists - check its network
            network_cmd = "timeout 5 docker inspect nginx --format '{{.HostConfig.NetworkMode}}' 2>/dev/null || echo 'bridge'"
            current_network = CommandExecuter.run_cmd(network_cmd, target_server, user)
            current_network = current_network.stdout.strip() if hasattr(current_network, 'stdout') else str(current_network).strip()
            
            if current_network != network_name:
                # Wrong network - recreate it!
                log(f"Nginx on wrong network ({current_network}), expected {network_name} - recreating")
                CommandExecuter.run_cmd("docker rm -f nginx", target_server, user)
                # Fall through to creation below
            elif DockerExecuter.is_container_running(container_name, target_server, user):
                log(f"Nginx container '{container_name}' already running on correct network")
                return True
            else:
                # Correct network but not running - start it
                CommandExecuter.run_cmd("docker start nginx", target_server, user)
                log(f"Started existing nginx container")
                return True
        
        # Create nginx container on the correct network
        log(f"Starting nginx container '{container_name}' on network '{network_name}'")
        
        cert_paths = NginxConfigGenerator._get_cert_paths(target_server)
        target_os = ResourceResolver.detect_target_os(target_server, user)
        
        # Get base nginx path
        if target_server == "localhost" or target_server is None:
            if target_os == "windows":
                nginx_base = "C:/local/nginx"
            else:
                nginx_base = "/etc/nginx"
        else:
            nginx_base = "/etc/nginx"
        
        # Build paths
        main_conf = f"{nginx_base}/nginx.conf"
        conf_dir = f"{nginx_base}/conf.d"
        stream_dir = f"{nginx_base}/stream.d"
        
        # For localhost, create directories
        if target_server == "localhost" or target_server is None:
          
            Path(conf_dir).mkdir(parents=True, exist_ok=True)
            Path(stream_dir).mkdir(parents=True, exist_ok=True)
            
            if not Path(main_conf).exists():
                NginxConfigGenerator._ensure_main_nginx_local(target_os)
        
        volumes = [
            f"{main_conf}:/etc/nginx/nginx.conf:ro",
            f"{conf_dir}:/etc/nginx/conf.d:ro",
            f"{stream_dir}:/etc/nginx/stream.d:ro",
            f"{cert_paths['etc']}:/etc/letsencrypt:ro",
            f"{cert_paths['ssl']}:/etc/nginx/ssl:ro",
            "/var/log/nginx:/var/log/nginx"
        ]
        
        ports = {
            "80": "80",
            "443": "443"
        }
        
        # Expose internal port for every service
        for service_name in services.keys():
            internal_port = ResourceResolver.get_internal_port(project, env, service_name)
            ports[str(internal_port)] = str(internal_port)

        try:
            DockerExecuter.run_container(
                image="nginx:alpine",
                name=container_name,
                network=network_name,  # ← This is already correct
                ports=ports,
                volumes=volumes,
                restart_policy="unless-stopped",
                server_ip=target_server,
                user=user
            )
            log(f"Started nginx container '{container_name}' on network '{network_name}'")
            return True
        except Exception as e:
            log(f"Failed to start nginx container: {e}")
            return False

    @staticmethod
    def _cf_get_zone_id(target_server: str, cf_token: str, zone_name: str) -> str | None:
        url = f"https://api.cloudflare.com/client/v4/zones?name={zone_name}&status=active"
        cmd = (
            f'curl -sS -X GET "{url}" '
            f'-H "Authorization: Bearer {cf_token}" '
            f'-H "Content-Type: application/json"'
        )
        out = CommandExecuter.run_cmd(cmd, target_server)
        try:
            data = json.loads(str(out))
            if data.get("success") and data.get("result"):
                return data["result"][0]["id"]
        except Exception as e:
            log(f"CF zone lookup parse error: {e}")
        return None

    @staticmethod
    def _cf_upsert_record(
        target_server: str,
        cf_token: str,
        zone_id: str,
        *,
        rtype: str,
        name: str,
        content: str,
        proxied: bool = True,
        ttl: int = 300
    ) -> None:
        base = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
        headers = f'-H "Authorization: Bearer {cf_token}" -H "Content-Type: application/json"'
        get_cmd = f'curl -sS -X GET "{base}?type={rtype}&name={name}" {headers}'
        gout = CommandExecuter.run_cmd(get_cmd, target_server)
        rec_id = None
        try:
            gd = json.loads(str(gout))
            if gd.get("success") and gd.get("result"):
                rec_id = gd["result"][0]["id"]
        except Exception as e:
            log(f"CF get record parse error for {name}: {e}")

        payload = json.dumps({"type": rtype, "name": name, "content": content, "proxied": proxied, "ttl": ttl})

        if rec_id:
            put_cmd = f'curl -sS -X PUT "{base}/{rec_id}" {headers} --data \'{payload}\''
            pout = CommandExecuter.run_cmd(put_cmd, target_server)
            try:
                pd = json.loads(str(pout))
                if not pd.get("success"):
                    log(f"CF update failed for {name}: {pout}")
                else:
                    log(f"CF updated {rtype} {name} -> {content} (proxied={proxied}, ttl={ttl})")
            except Exception:
                log(f"CF update parse error for {name}: {pout}")
        else:
            post_cmd = f'curl -sS -X POST "{base}" {headers} --data \'{payload}\''
            out = CommandExecuter.run_cmd(post_cmd, target_server)
            try:
                rd = json.loads(str(out))
                if not rd.get("success"):
                    log(f"CF create failed for {name}: {out}")
                else:
                    log(f"CF created {rtype} {name} -> {content} (proxied={proxied}, ttl={ttl})")
            except Exception:
                log(f"CF create parse error for {name}: {out}")

    @staticmethod
    def _cloudflare_upsert_records(
        target_server: str,
        *,
        cf_token: str,
        zone_name: str,
        records: List[Dict[str, str]],
        proxied: bool = True,
        ttl: int = 300
    ) -> None:
        zone_id = NginxConfigGenerator._cf_get_zone_id(target_server, cf_token, zone_name)
        if not zone_id:
            log(f"Cloudflare zone not found: {zone_name}")
            return
        for r in records:
            rtype = r.get("type", "A")
            name = r.get("name")
            value = r.get("value")
            if not (name and value):
                continue
            if rtype not in ("A", "AAAA", "CNAME"):
                log(f"Skipping unsupported record type {rtype} for {name}")
                continue
            NginxConfigGenerator._cf_upsert_record(
                target_server, cf_token, zone_id,
                rtype=rtype, name=name, content=value,
                proxied=proxied if rtype in ("A", "AAAA") else False,
                ttl=ttl,
            )

    # ----- Certificate management (containerized) -----

    @staticmethod
    def _provision_cert_containers_and_issue(
        target_server: str,
        *,
        domains: List[str],
        email: Optional[str],
        mode: str,
        cloudflare_api_token: Optional[str],
        apply_dns: bool,
    ) -> None:
        assert domains, "domains list must not be empty"
        m = mode.strip().lower()
        if m not in ("letsencrypt_dns_cloudflare", "letsencrypt_standalone", "selfsigned"):
            raise ValueError("mode must be 'letsencrypt_dns_cloudflare' | 'letsencrypt_standalone' | 'selfsigned'")

        cert_paths = NginxConfigGenerator._get_cert_paths(target_server)
        ResourceResolver.ensure_nginx_cert_directories(target_server, user="root")

        if apply_dns and cloudflare_api_token:
            lb_ipv4 = NginxConfigGenerator._detect_public_ipv4(target_server)
            if lb_ipv4:
                zone_to_records: Dict[str, List[Dict[str, str]]] = {}
                for d in domains:
                    if d.startswith("*."):
                        continue
                    zone = _registrable_zone(d)
                    zone_to_records.setdefault(zone, []).append({"type": "A", "name": d, "value": lb_ipv4})
                for zone, recs in zone_to_records.items():
                    try:
                        NginxConfigGenerator._cloudflare_upsert_records(
                            target_server,
                            cf_token=cloudflare_api_token,
                            zone_name=zone,
                            records=recs,
                            proxied=NginxConfigGenerator.CF_PROXIED_DEFAULT,
                            ttl=NginxConfigGenerator.CF_TTL_DEFAULT,
                        )
                    except Exception as e:
                        log(f"Cloudflare DNS upsert failed for zone {zone} (continuing): {e}")

        certbot_image = "certbot/certbot:latest"
        openssl_image = "frapsoft/openssl:latest"
        DockerExecuter.pull_image(certbot_image, server_ip=target_server)
        DockerExecuter.pull_image(openssl_image, server_ip=target_server)

        def stop_nginx():
            try:
                CommandExecuter.run_cmd(f"docker stop {NginxConfigGenerator.NGINX_CONTAINER}", target_server)
                log(f"Stopped nginx on {target_server}")
            except Exception as e:
                log(f"Could not stop nginx on {target_server}: {e}")

        def start_nginx():
            try:
                CommandExecuter.run_cmd(f"docker start {NginxConfigGenerator.NGINX_CONTAINER}", target_server)
                log(f"Started nginx on {target_server}")
            except Exception as e:
                log(f"Could not start nginx on {target_server}: {e}")

        def reload_nginx():
            try:
                CommandExecuter.run_cmd(f"docker exec {NginxConfigGenerator.NGINX_CONTAINER} nginx -s reload", target_server)
                log(f"Reloaded nginx on {target_server}")
            except Exception as e:
                log(f"Could not reload nginx on {target_server}: {e}")

        le_vols = [
            f"{cert_paths['etc']}:/etc/letsencrypt",
            f"{cert_paths['var']}:/var/lib/letsencrypt",
            f"{cert_paths['log']}:/var/log/letsencrypt",
        ]

        if m == "letsencrypt_dns_cloudflare":
            if not email:
                raise ValueError("email is required for Let's Encrypt DNS mode")
            if not cloudflare_api_token:
                raise ValueError("cloudflare_api_token is required for DNS-01 with Cloudflare")

            d_args: List[str] = []
            for d in domains:
                d_args.extend(["-d", d])

            cloudflare_creds_path = "/etc/letsencrypt/cloudflare.ini"
            shell_cmd = (
                "python -m pip install --no-cache-dir certbot-dns-cloudflare && "
                "mkdir -p /etc/letsencrypt && "
                f"sh -lc 'printf \"dns_cloudflare_api_token = %s\\n\" \"$CF_API_TOKEN\" > {cloudflare_creds_path}' && "
                f"chmod 600 {cloudflare_creds_path} && "
                "certbot certonly --non-interactive --agree-tos "
                f"--email {email} "
                "--dns-cloudflare "
                f"--dns-cloudflare-credentials {cloudflare_creds_path} "
                + " ".join(d_args)
            )
            env = {"CF_API_TOKEN": cloudflare_api_token}

            DockerExecuter.run_container_once(
                image=certbot_image,
                command=["sh", "-lc", shell_cmd],
                ports=None,
                volumes=le_vols,
                environment=env,
                network=None,
                server_ip=target_server,
            )
            for d in domains:
                log(f"LE DNS-01 via Cloudflare ensured for {d} on {target_server}")
            reload_nginx()
            return

        if m == "letsencrypt_standalone":
            if not email:
                raise ValueError("email is required for Let's Encrypt standalone mode")

            d_args: List[str] = []
            for d in domains:
                d_args.extend(["-d", d])

            publish = ["-p", "80:80"]
            challenge_args = ["--preferred-challenges", "http-01"]

            stop_nginx()
            
            try:
                DockerExecuter.run_container_once(
                    image=certbot_image,
                    command=[
                        "certonly", "--standalone",
                        "--non-interactive", "--agree-tos",
                        f"--email={email}",
                        *d_args,
                        *challenge_args,
                    ],
                    ports=publish,
                    volumes=le_vols,
                    environment=None,
                    network=None,
                    server_ip=target_server,
                )
                for d in domains:
                    log(f"LE cert ensured (standalone HTTP-01) for {d} on {target_server}")
            finally:
                start_nginx()
                reload_nginx()
            
            return

        # selfsigned
        for d in domains:
            key = f"{cert_paths['ssl']}/{d}.key"
            crt = f"{cert_paths['ssl']}/{d}.crt"
            subj = f"/CN={d}"
            san  = f"subjectAltName=DNS:{d}"

            DockerExecuter.run_container_once(
                image=openssl_image,
                command=["sh", "-c",
                         f'openssl req -x509 -nodes -days 365 -newkey rsa:2048 '
                         f'-keyout "{key}" -out "{crt}" -subj "{subj}" -addext "{san}"'],
                ports=None,
                volumes=[f"{cert_paths['ssl']}:/etc/nginx/ssl"],
                environment=None,
                network=None,
                server_ip=target_server,
            )
            log(f"Self-signed generated for {d} at {cert_paths['ssl']}/")

        reload_nginx()

    @staticmethod
    def _renew_certificates(
        target_server: str,
        *,
        project: Optional[str],
        env: Optional[str],
        service_name: Optional[str],
        email: Optional[str],
        cloudflare_api_token: Optional[str],
    ) -> bool:
        try:
            listing = CommandExecuter.run_cmd("ls -1 /etc/nginx/conf.d/*.conf 2>/dev/null", target_server)
        except Exception as e:
            log(f"Unable to list conf.d on {target_server}: {e}")
            return False

        conf_paths = [p.strip() for p in str(listing).splitlines() if p.strip()]
        if not conf_paths:
            log(f"No conf files on {target_server}:/etc/nginx/conf.d/")
            return False

        def match_name(path: str) -> bool:
            name = Path(path).name
            if project is not None and not name.startswith(f"{project}_"):
                return False
            if env is not None and f"_{env}_" not in name:
                return False
            if service_name is not None and not name.endswith(f"_{service_name}.conf"):
                return False
            return True

        conf_paths = [p for p in conf_paths if match_name(p)]
        if not conf_paths:
            log("No matching conf files for the given filters; nothing to renew.")
            return False

        re_server_name = re.compile(r"^\s*server_name\s+([^;]+);", re.MULTILINE)
        domains: List[str] = []
        for conf in conf_paths:
            try:
                text = CommandExecuter.run_cmd(f"cat {conf}", target_server)
            except Exception as e:
                log(f"Cannot read {conf} on {target_server}: {e}")
                continue
            m = re_server_name.search(str(text))
            if m:
                d = m.group(1).strip().split()[0]
                if d:
                    domains.append(d)

        domains = sorted(list({d for d in domains if d}))
        if not domains:
            log("No domains found in matching confs; nothing to renew.")
            return False

        mode = NginxConfigGenerator._detect_mode(email, cloudflare_api_token)

        if mode in ("letsencrypt_dns_cloudflare", "letsencrypt_standalone"):
            if not email:
                raise ValueError("email is required for Let's Encrypt renewal")
            NginxConfigGenerator._provision_cert_containers_and_issue(
                target_server=target_server,
                domains=domains,
                email=email,
                mode=mode,
                cloudflare_api_token=cloudflare_api_token,
                apply_dns=False,
            )
            return True

        NginxConfigGenerator._provision_cert_containers_and_issue(
            target_server=target_server,
            domains=domains,
            email=None,
            mode="selfsigned",
            cloudflare_api_token=None,
            apply_dns=False,
        )
        return True

    # ----- Nginx reload / firewall / OS detect -----

    @staticmethod
    def _reload_nginx(target_server: Optional[str]) -> None:
        try:
            CommandExecuter.run_cmd(f"docker exec {NginxConfigGenerator.NGINX_CONTAINER} nginx -s reload", target_server)
            log(f"Reloaded nginx in container '{NginxConfigGenerator.NGINX_CONTAINER}' on {target_server or 'local'}")
        except Exception as e:
            log(f"Failed to reload nginx: {e}")

    @staticmethod
    def _detect_remote_os(target_server: Optional[str]) -> str:
        if not target_server:
            return "unknown"
        try:
            out = CommandExecuter.run_cmd("uname -s", target_server)
            if out and "linux" in str(out).lower():
                return "linux"
        except Exception:
            pass
        try:
            out = CommandExecuter.run_cmd('powershell -Command "$PSVersionTable.PSVersion"', target_server)
            if out:
                return "windows"
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _ensure_firewall(target_server: Optional[str], admin_ip: Optional[str] = None) -> None:
        if not target_server or target_server == "localhost":
            log("Skipping firewall automation for localhost")
            return
        
        os_name = (NginxConfigGenerator._detect_remote_os(target_server)).lower()
        
        if not admin_ip:
            admin_ip = NginxConfigGenerator._detect_my_public_ip()
            if admin_ip:
                log(f"Detected admin IP: {admin_ip}")
        
        if not admin_ip:
            log("Cannot detect admin IP; firewall rules may lock you out. Provide admin_ip explicitly.")
            return
        
        cf_v4 = NginxConfigGenerator._curl_local("https://www.cloudflare.com/ips-v4")
        cf_v6 = NginxConfigGenerator._curl_local("https://www.cloudflare.com/ips-v6")
        cf_ranges = []
        for txt in (cf_v4 or "").splitlines():
            if txt.strip():
                cf_ranges.append(txt.strip())
        for txt in (cf_v6 or "").splitlines():
            if txt.strip():
                cf_ranges.append(txt.strip())
        
        if not cf_ranges:
            log("Could not fetch Cloudflare IP ranges; using default list.")
            cf_ranges = NginxConfigGenerator._default_cf_ranges()
        
        try:
            if os_name == "linux":
                CommandExecuter.run_cmd("ufw --force reset", target_server)
                CommandExecuter.run_cmd("ufw default deny incoming", target_server)
                CommandExecuter.run_cmd("ufw default allow outgoing", target_server)
                
                CommandExecuter.run_cmd(f"ufw allow from {admin_ip} to any port 22 proto tcp comment 'SSH admin only'", target_server)
                CommandExecuter.run_cmd(f"ufw allow from {admin_ip} to any port 443 proto tcp comment 'HTTPS admin'", target_server)
                CommandExecuter.run_cmd(f"ufw allow from {admin_ip} to any port 443 proto udp comment 'HTTP/3 admin'", target_server)
                
                for cidr in cf_ranges:
                    try:
                        CommandExecuter.run_cmd(f"ufw allow from {cidr} to any port 443 proto tcp comment 'CF-HTTPS'", target_server)
                        CommandExecuter.run_cmd(f"ufw allow from {cidr} to any port 443 proto udp comment 'CF-HTTP/3'", target_server)
                    except Exception as e:
                        log(f"Failed to add Cloudflare range {cidr}: {e}")
                
                CommandExecuter.run_cmd("ufw --force enable", target_server)
                log(f"Firewall locked down on {target_server}: 443 (CF+admin), 22 (admin only)")
                
            elif os_name == "windows":
                for rule_name in ["Allow443TCP", "Allow443UDP", "Allow22Admin", "Allow443TCPAdmin", "Allow443UDPAdmin", "Allow443TCPCF", "Allow443UDPCF"]:
                    CommandExecuter.run_cmd(
                        f'powershell -Command "Remove-NetFirewallRule -DisplayName {rule_name} -ErrorAction SilentlyContinue"',
                        target_server
                    )
                
                admin_ip_escaped = admin_ip.replace('"', '`"')
                CommandExecuter.run_cmd(
                    f'powershell -Command "New-NetFirewallRule -DisplayName Allow22Admin '
                    f'-Direction Inbound -LocalPort 22 -Protocol TCP -Action Allow '
                    f'-RemoteAddress {admin_ip_escaped}"',
                    target_server
                )
                
                CommandExecuter.run_cmd(
                    f'powershell -Command "New-NetFirewallRule -DisplayName Allow443TCPAdmin '
                    f'-Direction Inbound -LocalPort 443 -Protocol TCP -Action Allow '
                    f'-RemoteAddress {admin_ip_escaped}"',
                    target_server
                )
                CommandExecuter.run_cmd(
                    f'powershell -Command "New-NetFirewallRule -DisplayName Allow443UDPAdmin '
                    f'-Direction Inbound -LocalPort 443 -Protocol UDP -Action Allow '
                    f'-RemoteAddress {admin_ip_escaped}"',
                    target_server
                )
                
                cf_list = ",".join(cf_ranges)
                CommandExecuter.run_cmd(
                    f'powershell -Command "New-NetFirewallRule -DisplayName Allow443TCPCF '
                    f'-Direction Inbound -LocalPort 443 -Protocol TCP -Action Allow '
                    f'-RemoteAddress {cf_list}"',
                    target_server
                )
                CommandExecuter.run_cmd(
                    f'powershell -Command "New-NetFirewallRule -DisplayName Allow443UDPCF '
                    f'-Direction Inbound -LocalPort 443 -Protocol UDP -Action Allow '
                    f'-RemoteAddress {cf_list}"',
                    target_server
                )
                
                log(f"Firewall locked down on {target_server}: 443 (CF+admin), 22 (admin only)")
            else:
                log(f"Unknown remote OS for {target_server}; configure firewall manually.")
        except Exception as e:
            log(f"Firewall configuration failed on {target_server}: {e}")
    
    @staticmethod
    def _detect_my_public_ip() -> Optional[str]:
        try:
            val = str(CommandExecuter.run_cmd(
                'powershell -Command "(Invoke-WebRequest -Uri https://ifconfig.me -UseBasicParsing).Content.Trim()"',
                target_server=None
            )).strip()
            if val and "html" not in val.lower() and len(val) < 50:
                return val
        except Exception:
            pass
        
        try:
            val = str(CommandExecuter.run_cmd('curl -s ifconfig.me', target_server=None)).strip()
            if val and "html" not in val.lower() and len(val) < 50:
                return val
        except Exception:
            pass
        
        return None

    # ----- Nginx config generation -----

    @staticmethod
    def _merge_with_defaults(user_cfg: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(NginxConfigGenerator.DEFAULTS)
        for k, v in user_cfg.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        return merged

    @staticmethod
    def _get_upstream_servers(
        project: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        servers: List[Dict[str, Any]] = []
        
        server_ips = service_config.get("servers")
        
        if server_ips and isinstance(server_ips, list):
            pass
        else:
            log(f"Warning: No servers list provided for {service_name}, using localhost")
            server_ips = ["localhost"]
        
        dockerfile = service_config.get("dockerfile")
        container_ports = ResourceResolver.get_container_ports(service_name, dockerfile)
        container_port = container_ports[0] if container_ports else "8000"

        if len(server_ips) > 1:
            for server_ip in server_ips:
                host_port = ResourceResolver.get_host_port(project, env, service_name, container_port)
                servers.append({
                    "host": server_ip,
                    "port": host_port,
                    "weight": 1,
                    "max_fails": 3,
                    "fail_timeout": "30s",
                })
        else:
            container_name = ResourceResolver.get_container_name(project, env, service_name)
            servers.append({"host": container_name, "port": container_port, "weight": 1})
        
        return servers

    @staticmethod
    def _generate_main_config(nginx_config: Dict[str, Any]) -> str:
        cf_block = NginxConfigGenerator._render_cf_block(NginxConfigGenerator._default_cf_ranges())
        return f"""user nginx;
worker_processes auto;
error_log {nginx_config['error_log']} warn;
pid /var/run/nginx.pid;

events {{
    worker_connections {nginx_config['worker_connections']};
    use epoll;
    multi_accept on;
}}

stream {{
    include /etc/nginx/stream.d/*.conf;
}}

http {{
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    {cf_block}

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';

    access_log {nginx_config['access_log']} main;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout {nginx_config['keepalive_timeout']};
    types_hash_max_size 2048;
    client_max_body_size {nginx_config['client_max_body_size']};

    {'gzip on;' if nginx_config['gzip'] else 'gzip off;'}
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types {nginx_config['gzip_types']};

    limit_req_zone $binary_remote_addr zone=general:{nginx_config['rate_limit_zone_size']} rate={nginx_config['rate_limit']};

    include /etc/nginx/conf.d/*.conf;
}}"""

    @staticmethod
    def _generate_server_config(
        project: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        nginx_config: Dict[str, Any],
        upstream_servers: List[Dict[str, Any]],
    ) -> str:
        domain = service_config.get("domain", f"{service_name}.local")

        ssl_cert = nginx_config.get("ssl_cert") or service_config.get("ssl_cert")
        ssl_key  = nginx_config.get("ssl_key")  or service_config.get("ssl_key")
        ssl_enabled = bool(ssl_cert and ssl_key) or bool(service_config.get("domain"))

        if len(upstream_servers) > 1:
            backend = f"{service_name}_backend"
            upstream_block = NginxConfigGenerator._generate_upstream_config(service_name, upstream_servers, nginx_config) + "\n\n"
        else:
            s = upstream_servers[0]
            backend = f"{s['host']}:{s['port']}"
            upstream_block = ""

        location_block = NginxConfigGenerator._generate_location_block(backend, service_name, nginx_config)
        ssl_redirect   = NginxConfigGenerator._generate_ssl_redirect(ssl_enabled, nginx_config)
        static_cache   = NginxConfigGenerator._generate_static_cache_rules(nginx_config) if nginx_config.get("cache_static") else ""
        alt_svc_header = """add_header Alt-Svc 'h3=":443"';"""

        return f"""
# Project={project}, Env={env}, Service={service_name}
{upstream_block}server {{
    listen 80;
    {'listen 443 ssl http2;' if ssl_enabled else ''}
    {'listen 443 quic reuseport;' if ssl_enabled else ''}
    server_name {domain};

    {'ssl_certificate ' + ssl_cert + ';' if ssl_enabled and ssl_cert else ''}
    {'ssl_certificate_key ' + ssl_key + ';' if ssl_enabled and ssl_key else ''}
    {'ssl_protocols ' + nginx_config['ssl_protocols'] + ';' if ssl_enabled else ''}

    {alt_svc_header if ssl_enabled else ''}
    {'add_header QUIC-Status $quic;' if ssl_enabled else ''}

    access_log /var/log/nginx/{service_name}_access.log main;
    error_log  /var/log/nginx/{service_name}_error.log warn;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    keepalive_timeout 120;

    {ssl_redirect}

    {location_block}

    {static_cache}

    {nginx_config.get('custom_config', '')}
}}
"""

    @staticmethod
    def _generate_upstream_config(
        service_name: str,
        upstream_servers: List[Dict[str, Any]],
        nginx_config: Dict[str, Any],
    ) -> str:
        method = nginx_config.get("load_balance_method", "least_conn")
        lb_line = ""
        if method == "least_conn":
            lb_line = "least_conn;"
        elif method == "ip_hash":
            lb_line = "ip_hash;"
        elif method == "random":
            lb_line = "random;"
        lines = [f"upstream {service_name}_backend {{", f"    {lb_line}" if lb_line else ""]
        for s in upstream_servers:
            extra = []
            if s.get("weight"):
                extra.append(f"weight={s['weight']}")
            if s.get("max_fails"):
                extra.append(f"max_fails={s['max_fails']}")
            if s.get("fail_timeout"):
                extra.append(f"fail_timeout={s['fail_timeout']}")
            extra_str = " ".join(extra)
            lines.append(f"    server {s['host']}:{s['port']}{' ' + extra_str if extra_str else ''};")
        lines.append("}")
        return "\n".join([l for l in lines if l.strip()])

    @staticmethod
    def _generate_location_block(
        backend: str,
        service_name: str,
        nginx_config: Dict[str, Any],
    ) -> str:
        rate_limit = ""
        if nginx_config.get("rate_limit"):
            rate_limit = f"limit_req zone=general burst={nginx_config.get('rate_limit_burst', 20)} nodelay;"

        basic_auth = ""
        if nginx_config.get("basic_auth"):
            basic_auth = 'auth_basic "Restricted";\n        auth_basic_user_file /etc/nginx/.htpasswd;'

        websocket_support = ""
        if nginx_config.get("websocket"):
            websocket_support = """proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";"""

        health = NginxConfigGenerator._generate_health_check(nginx_config) if nginx_config.get("health_check") else ""

        return f"""location / {{
        {rate_limit}
        {basic_auth}

        proxy_pass http://{backend};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;

        {websocket_support}

        proxy_connect_timeout {nginx_config.get('proxy_timeout', 300)}s;
        proxy_send_timeout {nginx_config.get('proxy_timeout', 300)}s;
        proxy_read_timeout {nginx_config.get('proxy_timeout', 300)}s;

        proxy_buffering off;
        proxy_request_buffering off;

        {health}
    }}"""

    @staticmethod
    def _generate_ssl_redirect(ssl_enabled: bool, nginx_config: Dict[str, Any]) -> str:
        if not ssl_enabled or not nginx_config.get("ssl_redirect", True):
            return ""
        return """if ($scheme != "https") {
        return 301 https://$server_name$request_uri;
    }"""

    @staticmethod
    def _generate_static_cache_rules(nginx_config: Dict[str, Any]) -> str:
        expires = nginx_config.get("cache_static_expires", "30d")
        return f"""# Cache static files
    location ~* \.(jpg|jpeg|png|gif|ico|css|js|svg|woff|woff2|ttf|eot)$ {{
        expires {expires};
        add_header Cache-Control "public, immutable";
        access_log off;
    }}"""

    @staticmethod
    def _generate_health_check(nginx_config: Dict[str, Any]) -> str:
        endpoint = nginx_config.get("health_check", "/health")
        return f"""
        location {endpoint} {{
            access_log off;
            return 200 "healthy";
            add_header Content-Type text/plain;
        }}"""
    

    @staticmethod
    def setup_cloudflare_load_balancer(
        domain: str,
        origin_ips: List[str],
        cloudflare_api_token: str,
        geo_steering: bool = True
    ) -> bool:
        """
        Create Cloudflare Load Balancer for multi-zone deployments.
        Needs LB to be enabled in cloudflare (cost 5$ pm).
        
        Args:
            domain: Domain to load balance (e.g., 'api.example.com')
            origin_ips: List of nginx LB IPs from each zone
            cloudflare_api_token: Cloudflare API token
            geo_steering: Enable geo-routing (routes users to nearest origin)
            
        Returns:
            True if successful
        """
        import json
        
        zone_name = _registrable_zone(domain)
        
        # Get zone ID
        zone_id = NginxConfigGenerator._cf_get_zone_id("localhost", cloudflare_api_token, zone_name)
        if not zone_id:
            log(f"Zone {zone_name} not found in Cloudflare")
            return False
        
        # Get account ID
        cmd = (
            f'curl -sS -X GET "https://api.cloudflare.com/client/v4/zones/{zone_id}" '
            f'-H "Authorization: Bearer {cloudflare_api_token}"'
        )
        result = CommandExecuter.run_cmd(cmd, "localhost")
        data = json.loads(str(result))
        account_id = data['result']['account']['id']
        
        log(f"Creating Cloudflare Load Balancer for {domain}")
        
        # 1. Create health monitor
        monitor_data = {
            "type": "https",
            "description": f"Health check for {domain}",
            "method": "GET",
            "path": "/health",
            "port": 443,
            "interval": 60,
            "retries": 2,
            "timeout": 5,
            "expected_codes": "200"
        }
        
        cmd = (
            f'curl -sS -X POST "https://api.cloudflare.com/client/v4/accounts/{account_id}/load_balancers/monitors" '
            f'-H "Authorization: Bearer {cloudflare_api_token}" '
            f'-H "Content-Type: application/json" '
            f"--data '{json.dumps(monitor_data)}'"
        )
        
        result = CommandExecuter.run_cmd(cmd, "localhost")
        monitor_id = json.loads(str(result))['result']['id']
        log(f"  Created health monitor: {monitor_id}")
        
        # 2. Create origin pools (one per zone)
        pool_ids = []
        for idx, ip in enumerate(origin_ips):
            domain_safe = domain.replace('.', '_')
            ip_safe = ip.replace('.', '_')
            
            pool_data = {
                "name": f"{domain_safe}_pool_{idx}",
                "enabled": True,
                "monitor": monitor_id,
                "origins": [{
                    "name": f"origin_{ip_safe}",
                    "address": ip,
                    "enabled": True,
                    "weight": 1
                }]
            }
            
            cmd = (
                f'curl -sS -X POST "https://api.cloudflare.com/client/v4/accounts/{account_id}/load_balancers/pools" '
                f'-H "Authorization: Bearer {cloudflare_api_token}" '
                f'-H "Content-Type: application/json" '
                f"--data '{json.dumps(pool_data)}'"
            )
            
            result = CommandExecuter.run_cmd(cmd, "localhost")
            pool_id = json.loads(str(result))['result']['id']
            pool_ids.append(pool_id)
            log(f"  Created pool for {ip}: {pool_id}")
        
        # 3. Create load balancer
        lb_data = {
            "name": domain,
            "enabled": True,
            "ttl": 30,
            "steering_policy": "geo" if geo_steering else "random",
            "proxied": True,
            "default_pools": pool_ids,
            "fallback_pool": pool_ids[0]
        }
        
        cmd = (
            f'curl -sS -X POST "https://api.cloudflare.com/client/v4/zones/{zone_id}/load_balancers" '
            f'-H "Authorization: Bearer {cloudflare_api_token}" '
            f'-H "Content-Type: application/json" '
            f"--data '{json.dumps(lb_data)}'"
        )
        
        result = CommandExecuter.run_cmd(cmd, "localhost")
        data = json.loads(str(result))
        
        if not data.get("success"):
            log(f"Failed to create load balancer: {data}")
            return False
        
        log(f"  ✓ Load Balancer configured")
        log(f"  Strategy: {'Geo-routing' if geo_steering else 'Random'}")
        log(f"  Health checks: Every 60s on /health")
        
        return True
    

    @staticmethod
    def setup_cloudflare_load_balancer_with_zones(
        domain: str,
        zone_to_ips: Dict[str, List[str]],
        cloudflare_api_token: str,
        geo_steering: bool = True
    ) -> bool:
        """
        Create Cloudflare Load Balancer with proper intra-zone load balancing.
        
        Creates one origin pool per zone, with ALL servers in that zone as origins.
        This provides:
        - Inter-zone routing via Cloudflare geo-steering
        - Intra-zone load balancing across all servers within each zone
        
        Args:
            domain: Domain to load balance (e.g., 'api.example.com')
            zone_to_ips: Dict mapping zone name to list of server IPs
                        e.g., {"lon1": ["1.2.3.4", "1.2.3.5"], "sgp1": ["9.10.11.12"]}
            cloudflare_api_token: Cloudflare API token
            geo_steering: Enable geo-routing (routes users to nearest zone)
            
        Returns:
            True if successful
            
        Example:
            zone_to_ips = {
                "lon1": ["1.2.3.4", "1.2.3.5", "1.2.3.6"],
                "sgp1": ["9.10.11.12", "9.10.11.13", "9.10.11.14"]
            }
            setup_cloudflare_load_balancer_with_zones("api.example.com", zone_to_ips, token)
        """
        import json
        
        zone_name = _registrable_zone(domain)
        
        # Get zone ID
        zone_id = NginxConfigGenerator._cf_get_zone_id("localhost", cloudflare_api_token, zone_name)
        if not zone_id:
            log(f"Zone {zone_name} not found in Cloudflare")
            return False
        
        # Get account ID
        cmd = (
            f'curl -sS -X GET "https://api.cloudflare.com/client/v4/zones/{zone_id}" '
            f'-H "Authorization: Bearer {cloudflare_api_token}"'
        )
        result = CommandExecuter.run_cmd(cmd, "localhost")
        data = json.loads(str(result))
        account_id = data['result']['account']['id']
        
        log(f"Creating Cloudflare Load Balancer for {domain} with intra-zone balancing")
        
        # 1. Create health monitor (one for all pools)
        monitor_data = {
            "type": "https",
            "description": f"Health check for {domain}",
            "method": "GET",
            "path": "/health",
            "port": 443,
            "interval": 60,
            "retries": 2,
            "timeout": 5,
            "expected_codes": "200"
        }
        
        cmd = (
            f'curl -sS -X POST "https://api.cloudflare.com/client/v4/accounts/{account_id}/load_balancers/monitors" '
            f'-H "Authorization: Bearer {cloudflare_api_token}" '
            f'-H "Content-Type: application/json" '
            f"--data '{json.dumps(monitor_data)}'"
        )
        
        result = CommandExecuter.run_cmd(cmd, "localhost")
        monitor_id = json.loads(str(result))['result']['id']
        log(f"  Created health monitor: {monitor_id}")
        
        # 2. Create one origin pool per zone, with ALL servers in that zone
        pool_ids = []
        
        for zone_name_do, ips in zone_to_ips.items():
            domain_safe = domain.replace('.', '_')
            zone_safe = zone_name_do.replace('-', '_')
            
            # Build origins list for this zone (ALL servers)
            origins = []
            for ip in ips:
                ip_safe = ip.replace('.', '_')
                origins.append({
                    "name": f"origin_{zone_safe}_{ip_safe}",
                    "address": ip,
                    "enabled": True,
                    "weight": 1  # Equal weight for all servers in the zone
                })
            
            pool_data = {
                "name": f"{domain_safe}_pool_{zone_safe}",
                "description": f"Pool for {zone_name_do} zone with {len(ips)} server(s)",
                "enabled": True,
                "monitor": monitor_id,
                "origins": origins,
                # Intra-zone load balancing settings
                "notification_email": "",
                "load_shedding": {
                    "default_percent": 0,
                    "default_policy": "random",
                    "session_percent": 0,
                    "session_policy": "hash"
                }
            }
            
            cmd = (
                f'curl -sS -X POST "https://api.cloudflare.com/client/v4/accounts/{account_id}/load_balancers/pools" '
                f'-H "Authorization: Bearer {cloudflare_api_token}" '
                f'-H "Content-Type: application/json" '
                f"--data '{json.dumps(pool_data)}'"
            )
            
            result = CommandExecuter.run_cmd(cmd, "localhost")
            pool_result = json.loads(str(result))
            
            if not pool_result.get("success"):
                log(f"Failed to create pool for {zone_name_do}: {pool_result}")
                continue
                
            pool_id = pool_result['result']['id']
            pool_ids.append(pool_id)
            log(f"  Created pool for {zone_name_do} ({len(ips)} origins): {pool_id}")
        
        if not pool_ids:
            log("Failed to create any origin pools")
            return False
        
        # 3. Create load balancer with all pools
        lb_data = {
            "name": domain,
            "description": f"Multi-zone LB with intra-zone balancing ({len(zone_to_ips)} zones)",
            "enabled": True,
            "ttl": 30,
            "steering_policy": "geo" if geo_steering else "random",
            "proxied": True,
            "default_pools": pool_ids,
            "fallback_pool": pool_ids[0],
            "session_affinity": "none",  # Can be "cookie" or "ip_cookie" for sticky sessions
            "session_affinity_ttl": 1800
        }
        
        cmd = (
            f'curl -sS -X POST "https://api.cloudflare.com/client/v4/zones/{zone_id}/load_balancers" '
            f'-H "Authorization: Bearer {cloudflare_api_token}" '
            f'-H "Content-Type: application/json" '
            f"--data '{json.dumps(lb_data)}'"
        )
        
        result = CommandExecuter.run_cmd(cmd, "localhost")
        data = json.loads(str(result))
        
        if not data.get("success"):
            log(f"Failed to create load balancer: {data}")
            return False
        
        log(f"  ✓ Load Balancer configured")
        log(f"  Total zones: {len(zone_to_ips)}")
        total_origins = sum(len(ips) for ips in zone_to_ips.values())
        log(f"  Total origins: {total_origins}")
        log(f"  Strategy: {'Geo-routing to nearest zone' if geo_steering else 'Random zone selection'}")
        log(f"  Intra-zone: Round-robin across all servers in selected zone")
        log(f"  Health checks: HTTPS /health every 60s on port 443")
        
        return True