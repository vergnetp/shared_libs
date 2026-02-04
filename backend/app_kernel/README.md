# app_kernel.testing

Self-testing infrastructure for kernel-based apps. Run functional tests against your own HTTP endpoints with SSE progress streaming, cancellation support, and structured reports.

**Kernel provides:** report accumulation, SSE consumption, authenticated HTTP client, route auto-mounting.
**Apps provide:** test cases, cleanup logic, domain-specific API methods.

## Quick Start

### 1. Write your test runner

```python
# my_app/test_runner.py
from app_kernel.testing import TestReport, TestApiClient
from app_kernel.tasks import TaskStream

class MyClient(TestApiClient):
    """App-specific API methods on top of generic HTTP client."""
    
    async def create_widget(self, name, color="blue"):
        return await self.post("/widgets", json={"name": name, "color": color})
    
    async def get_widgets(self):
        return await self.get("/widgets")
    
    async def delete_widget(self, widget_id):
        return await self.delete(f"/widgets/{widget_id}")


async def run_functional_tests(base_url: str, auth_token: str):
    stream = TaskStream("functional-test")
    yield stream.task_id_event()
    
    api = MyClient(base_url, auth_token, outer_task_id=stream.task_id)
    report = TestReport()
    
    try:
        # --- Test 1: Create widget ---
        stream("=== Test 1: Create Widget ===")
        yield stream.log()
        
        result = await api.create_widget("test-widget")
        report.add_result("create_widget", "id" in result)
        
        stream.check()  # Raises Cancelled if user cancelled
        
        # --- Test 2: List widgets ---
        stream("=== Test 2: List Widgets ===")
        yield stream.log()
        
        widgets = await api.get_widgets()
        report.add_result("list_widgets", len(widgets) > 0)
        
    except Cancelled:
        report.add_error("test_suite", "Cancelled by user")
    except Exception as e:
        report.add_error("test_suite", str(e))
    finally:
        # Cleanup
        stream("=== Cleanup ===")
        yield stream.log()
    
    stream(report.summary_line())
    yield stream.log()
    yield stream.complete(report.all_passed, report=report.to_dict())
```

### 2. Wire it up

```python
# main.py
from app_kernel import create_service, ServiceConfig
from .test_runner import run_functional_tests

app = create_service(
    name="my_service",
    routers=[widgets_router],
    config=ServiceConfig.from_env(),
    test_runners=[run_functional_tests],
)
# → POST /test/functional-tests is now live (admin only, SSE, cancellable)
```

Endpoint names are derived from function names: strip `run_` prefix, replace `_` with `-`.

| Function name | Endpoint |
|---------------|----------|
| `run_functional_tests` | `POST /test/functional-tests` |
| `run_smoke` | `POST /test/smoke` |
| `run_integration_suite` | `POST /test/integration-suite` |

Multiple runners are supported:

```python
app = create_service(
    ...
    test_runners=[run_functional_tests, run_smoke],
)
# → POST /test/functional-tests
# → POST /test/smoke
```

### 3. Run tests

```bash
curl -X POST http://localhost:8000/test/functional-tests \
  -H "Authorization: Bearer <admin_token>" \
  --no-buffer
```

Response is an SSE stream:

```
event: task_id
data: {"task_id": "functional-test-a1b2c3d4"}

event: log
data: {"message": "[14:30:01] === Test 1: Create Widget ===", "level": "info"}

event: log
data: {"message": "[14:30:02] === Test 2: List Widgets ===", "level": "info"}

event: complete
data: {"success": true, "report": {"summary": {"total_tests": 2, "passed": 2, ...}}}
```

Cancel mid-flight:

```bash
curl -X POST http://localhost:8000/tasks/functional-test-a1b2c3d4/cancel \
  -H "Authorization: Bearer <admin_token>"
```

## Runner Contract

**Signature:** `async def my_runner(base_url: str, auth_token: str) -> AsyncIterator[str]`

The kernel injects:
- `base_url` — detected from the incoming request (e.g. `http://localhost:8000/api/v1`)
- `auth_token` — the admin's bearer token

Everything else (env var checks, path resolution, external config) is the runner's responsibility. If something's wrong, raise early — before the first `yield`.

## SSE-Consuming Client

`TestApiClient` provides both standard and SSE-consuming HTTP methods. SSE methods consume the stream, propagate cancellation, and return structured results.

```python
class DeployApiClient(TestApiClient):
    async def deploy_zip(self, project, service, zip_bytes, **kw):
        body = {"project_name": project, "service_name": service, ...}
        return await self.stream_post("/deployments", json=body)
    
    async def deploy_image(self, project, service, image_bytes, image_name, **kw):
        files = {"file": (image_name, image_bytes, "application/octet-stream")}
        data = {"project_name": project, "service_name": service}
        return await self.stream_upload("/deployments/upload", files=files, data=data)
    
    async def get_snapshots(self):
        return await self.get("/snapshots")
```

### consume_sse Event Handling

Kernel-standard events get special handling:

| Event | Behavior |
|-------|----------|
| `task_id` | Captured for cancel propagation |
| `log` | Appended to `result["_logs"]`, triggers `on_log` callback |
| `complete` | Merged into result dict |
| *anything else* | Captured as `result[event_name] = data` |

The `on_log` callback lets you forward inner progress to an outer stream:

```python
async def run_tests(base_url: str, auth_token: str):
    stream = TaskStream("functional-test")
    api = MyClient(base_url, auth_token, outer_task_id=stream.task_id)
    
    def forward_log(msg, level):
        stream(f"  [inner] {msg}")
    
    result = await api.stream_post("/long-operation", json={...}, on_log=forward_log)
```

## Configuration

| Setting | Default | Env Var | Description |
|---------|---------|---------|-------------|
| `enable_test_routes` | `False` | `KERNEL_ENABLE_TESTS` | Enable test endpoints. Auto-set `True` when `test_runners` is provided to `create_service`. |

---

## API Reference

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `TestReport`

Accumulates pass/fail/skip test results with timing.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `add_result` | `test_name: str`, `success: bool`, `duration: float=None`, `details: dict=None` | | Recording | Record a test result (pass or fail). |
| | `add_error` | `test_name: str`, `error: str` | | Recording | Record a test failure with error message. Also calls add_result with success=False. |
| | `add_skip` | `test_name: str`, `reason: str` | | Recording | Record a skipped test (not counted as pass or fail). |
| `@property` | `total` | | `int` | Computed | Total number of test results. |
| `@property` | `passed` | | `int` | Computed | Number of passed tests. |
| `@property` | `failed` | | `int` | Computed | Number of failed tests (counted - passed). |
| `@property` | `skipped` | | `int` | Computed | Number of skipped tests. |
| `@property` | `counted` | | `int` | Computed | Tests that count toward pass/fail (total - skipped). |
| `@property` | `success_rate` | | `float` | Computed | Pass rate as percentage (0.0–100.0). |
| `@property` | `duration` | | `float` | Computed | Elapsed time since report creation in seconds. |
| `@property` | `all_passed` | | `bool` | Computed | True if no failures and at least one counted test. |
| | `to_dict` | | `dict` | Output | Full report as JSON-serializable dict with summary, results, and errors. |
| | `summary_line` | | `str` | Output | One-line summary for logging: "Tests: N \| Passed: N \| Failed: N \| ..." |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | | | Initialization | Initializes empty report with start_time, results list, and errors list. |

</details>

<br>

</div>


<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `TestApiClient`

Authenticated HTTP client for self-testing via own API routes. Subclass to add domain-specific methods.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `get` | `path: str`, `params: dict=None`, `timeout: float=None` | `Any` | HTTP | GET request, returns parsed JSON. |
| | `post` | `path: str`, `json: dict=None`, `**kwargs` | `Any` | HTTP | POST request (non-streaming), returns parsed JSON. |
| | `put` | `path: str`, `json: dict=None`, `**kwargs` | `Any` | HTTP | PUT request, returns parsed JSON. |
| | `patch` | `path: str`, `json: dict=None`, `**kwargs` | `Any` | HTTP | PATCH request, returns parsed JSON. |
| | `delete` | `path: str`, `params: dict=None`, `timeout: float=None` | `Any` | HTTP | DELETE request (non-streaming), returns parsed JSON. |
| | `stream_post` | `path: str`, `json: dict=None`, `**kwargs` | `dict` | SSE | POST that consumes an SSE response stream. |
| | `stream_get` | `path: str`, `**kwargs` | `dict` | SSE | GET that consumes an SSE response stream. |
| | `stream_delete` | `path: str`, `**kwargs` | `dict` | SSE | DELETE that consumes an SSE response stream. |
| | `stream_upload` | `path: str`, `files: dict`, `data: dict=None`, `timeout: float=None`, `on_log: Callable=None` | `dict` | SSE | Multipart upload POST that consumes an SSE response stream. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `base_url: str`, `auth_token: str`, `outer_task_id: str=None`, `timeout: float=600.0` | | Initialization | Initializes client with base URL, auth headers, timeout, and optional cancel task ID. |
| | `_url` | `path: str` | `str` | Internal | Build full URL from path. |
| `@staticmethod` | `_strip_none` | `d: dict` | `dict` | Internal | Remove None values from dict (Pydantic v2 compat). |
| | `_stream_request` | `method: str`, `path: str`, `json: dict=None`, `files: dict=None`, `data: dict=None`, `headers: dict=None`, `timeout: float=None`, `on_log: Callable=None` | `dict` | Internal | Generic SSE-consuming request used by all stream_* methods. |

</details>

<br>

</div>


<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### function `consume_sse`

Consume an SSE stream from an httpx response with cancel propagation.

<details>
<summary><strong>Signature</strong></summary>

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `response` | `httpx.Response` | *(required)* | Streaming httpx response to consume. |
| `outer_task_id` | `str` | `None` | TaskStream ID for cancel propagation to inner task. |
| `on_log` | `Callable[[str, str], None]` | `None` | Callback(message, level) invoked for each log event. |

**Returns:** `dict` — At minimum `{"success": bool, "_logs": list}`. Complete event data merged in. Any app-specific events captured as `result[event_name] = data`.

**Raises:** `Cancelled` if outer task was cancelled during consumption.

</details>

<br>

</div>
