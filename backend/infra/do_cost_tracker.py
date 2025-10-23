import os
import json
from typing import Dict, Any, Optional
from datetime import datetime

try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .logger import Logger
except ImportError:
    from logger import Logger


def log(msg):
    Logger.log(msg)


class DOCostTracker:
    """Track DigitalOcean costs via API"""
    
    @staticmethod
    def get_balance() -> Optional[Dict[str, Any]]:
        """
        Get current account balance and billing info.
        
        Returns:
            {
                'month_to_date_balance': '-45.23',
                'account_balance': '-45.23',
                'month_to_date_usage': '45.23',
                'generated_at': '2025-10-06T10:30:00Z'
            }
        """
        token = os.getenv("DO_API_TOKEN")
        if not token:
            log("Warning: DO_API_TOKEN not set, cannot fetch costs")
            return None
        
        try:
            cmd = (
                f'curl -sS -X GET '
                f'"https://api.digitalocean.com/v2/customers/my/balance" '
                f'-H "Authorization: Bearer {token}"'
            )
            
            result = CommandExecuter.run_cmd(cmd, 'localhost')
            
            if hasattr(result, 'stdout'):
                data = json.loads(result.stdout)
            else:
                data = json.loads(str(result))
            
            return data
            
        except Exception as e:
            log(f"Error fetching balance: {e}")
            return None
    
    @staticmethod
    def get_billing_history(months: int = 1) -> Optional[Dict[str, Any]]:
        """
        Get billing history.
        
        Args:
            months: Number of months to fetch (1-12)
            
        Returns:
            {
                'billing_history': [
                    {
                        'description': 'Invoice for October 2025',
                        'amount': '123.45',
                        'invoice_id': 'INV-12345',
                        'date': '2025-10-01T00:00:00Z',
                        'type': 'Invoice'
                    }
                ]
            }
        """
        token = os.getenv("DO_API_TOKEN")
        if not token:
            return None
        
        try:
            cmd = (
                f'curl -sS -X GET '
                f'"https://api.digitalocean.com/v2/customers/my/billing_history" '
                f'-H "Authorization: Bearer {token}"'
            )
            
            result = CommandExecuter.run_cmd(cmd, 'localhost')
            
            if hasattr(result, 'stdout'):
                data = json.loads(result.stdout)
            else:
                data = json.loads(str(result))
            
            return data
            
        except Exception as e:
            log(f"Error fetching billing history: {e}")
            return None
    
    @staticmethod
    def get_droplet_costs() -> Dict[str, float]:
        """
        Calculate costs per droplet based on current pricing.
        
        Returns:
            Dict mapping droplet_id -> monthly_cost
        """
        token = os.getenv("DO_API_TOKEN")
        if not token:
            return {}
        
        try:
            # Get all droplets
            cmd = (
                f'curl -sS -X GET '
                f'"https://api.digitalocean.com/v2/droplets?per_page=200" '
                f'-H "Authorization: Bearer {token}"'
            )
            
            result = CommandExecuter.run_cmd(cmd, 'localhost')
            
            if hasattr(result, 'stdout'):
                data = json.loads(result.stdout)
            else:
                data = json.loads(str(result))
            
            droplets = data.get('droplets', [])
            
            costs = {}
            for droplet in droplets:
                droplet_id = str(droplet['id'])
                # DigitalOcean provides monthly price in the size object
                monthly_cost = droplet.get('size', {}).get('price_monthly', 0)
                costs[droplet_id] = float(monthly_cost)
            
            return costs
            
        except Exception as e:
            log(f"Error fetching droplet costs: {e}")
            return {}
    
    @staticmethod
    def estimate_monthly_cost() -> float:
        """
        Estimate monthly cost based on current resources.
        
        Returns:
            Estimated monthly cost in USD
        """
        droplet_costs = DOCostTracker.get_droplet_costs()
        
        if not droplet_costs:
            return 0.0
        
        total = sum(droplet_costs.values())
        return total
    
    @staticmethod
    def format_cost_summary() -> str:
        """
        Format cost summary for display.
        
        Returns:
            Formatted string with cost information
        """
        balance_data = DOCostTracker.get_balance()
        estimated_monthly = DOCostTracker.estimate_monthly_cost()
        
        if not balance_data:
            return "Cost tracking unavailable (set DO_API_TOKEN)"
        
        # Extract values
        mtd_usage = float(balance_data.get('month_to_date_usage', '0'))
        account_balance = float(balance_data.get('account_balance', '0'))
        
        # Calculate days in month
        now = datetime.now()
        current_day = now.day
        
        # Simple days-in-month calculation
        if now.month in [1, 3, 5, 7, 8, 10, 12]:
            days_in_month = 31
        elif now.month in [4, 6, 9, 11]:
            days_in_month = 30
        else:
            # February
            year = now.year
            days_in_month = 29 if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0) else 28
        
        # Project end-of-month cost based on current usage rate
        daily_rate = mtd_usage / current_day if current_day > 0 else 0
        projected_monthly = daily_rate * days_in_month
        
        lines = [
            "\nDigitalOcean Costs:",
            f"  Month-to-Date: ${mtd_usage:.2f}",
            f"  Daily Rate: ${daily_rate:.2f}/day",
            f"  Projected Monthly: ${projected_monthly:.2f}",
            f"  Current Resources: ${estimated_monthly:.2f}/month",
            f"  Account Balance: ${abs(account_balance):.2f}"
        ]
        
        return "\n".join(lines)
    
    @staticmethod
    def get_cost_breakdown() -> Dict[str, Any]:
        """
        Get detailed cost breakdown.
        
        Returns:
            {
                'month_to_date': 45.23,
                'projected_monthly': 150.00,
                'estimated_monthly': 144.00,
                'droplet_costs': {'12345': 12.00, '67890': 6.00},
                'daily_rate': 1.50
            }
        """
        balance_data = DOCostTracker.get_balance()
        droplet_costs = DOCostTracker.get_droplet_costs()
        estimated_monthly = sum(droplet_costs.values())
        
        if not balance_data:
            return {
                'month_to_date': 0.0,
                'projected_monthly': 0.0,
                'estimated_monthly': estimated_monthly,
                'droplet_costs': droplet_costs,
                'daily_rate': 0.0
            }
        
        mtd_usage = float(balance_data.get('month_to_date_usage', '0'))
        
        now = datetime.now()
        current_day = now.day
        daily_rate = mtd_usage / current_day if current_day > 0 else 0
        
        # Days in month
        if now.month in [1, 3, 5, 7, 8, 10, 12]:
            days_in_month = 31
        elif now.month in [4, 6, 9, 11]:
            days_in_month = 30
        else:
            year = now.year
            days_in_month = 29 if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0) else 28
        
        projected_monthly = daily_rate * days_in_month
        
        return {
            'month_to_date': mtd_usage,
            'projected_monthly': projected_monthly,
            'estimated_monthly': estimated_monthly,
            'droplet_costs': droplet_costs,
            'daily_rate': daily_rate,
            'account_balance': float(balance_data.get('account_balance', '0'))
        }