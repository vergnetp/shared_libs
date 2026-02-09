"""
Test report accumulator.

Tracks pass/fail/skip results with timing. Produces summary dicts
suitable for SSE streaming or JSON responses.

Usage:
    report = TestReport()
    report.add_result("create_widget", True, duration=1.2)
    report.add_error("delete_widget", "404 Not Found")
    report.add_skip("billing_test", "Stripe not configured")
    print(report.summary_line())
    # Tests: 3 | Passed: 1 | Failed: 1 | Skipped: 1 | Rate: 50.0% | Duration: 4.21s
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List


class TestReport:
    """Accumulates test results."""
    
    def __init__(self):
        self.start_time = time.time()
        self.results: List[Dict] = []
        self.errors: List[Dict] = []
        
    def add_result(self, test_name: str, success: bool, duration: float = None, 
                   details: Dict[str, Any] = None):
        """Record a test result (pass or fail)."""
        self.results.append({
            "test": test_name,
            "success": success,
            "duration": duration,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        
    def add_error(self, test_name: str, error: str):
        """Record a test failure with error message."""
        self.errors.append({
            "test": test_name,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.add_result(test_name, False, details={"error": error})
    
    def add_skip(self, test_name: str, reason: str):
        """Record a skipped test (not counted as pass or fail)."""
        self.results.append({
            "test": test_name,
            "success": None,
            "skipped": True,
            "details": {"reason": reason},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    
    # --- Computed properties ---
    
    @property
    def total(self) -> int:
        return len(self.results)
    
    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r["success"] is True)
    
    @property
    def failed(self) -> int:
        return self.counted - self.passed
    
    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.get("skipped"))
    
    @property
    def counted(self) -> int:
        """Tests that count (total minus skipped)."""
        return self.total - self.skipped
    
    @property
    def success_rate(self) -> float:
        return (self.passed / self.counted * 100) if self.counted else 0.0
    
    @property
    def duration(self) -> float:
        return time.time() - self.start_time
    
    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.counted > 0
    
    # --- Output ---
        
    def to_dict(self) -> Dict:
        """Full report as JSON-serializable dict."""
        return {
            "summary": {
                "total_tests": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "skipped": self.skipped,
                "success_rate": f"{self.success_rate:.1f}%",
                "total_duration": f"{self.duration:.2f}s",
            },
            "results": self.results,
            "errors": self.errors,
        }
    
    def summary_line(self) -> str:
        """One-line summary for logging."""
        line = (
            f"Tests: {self.total} | Passed: {self.passed} | Failed: {self.failed} | "
            f"Skipped: {self.skipped} | Rate: {self.success_rate:.1f}% | "
            f"Duration: {self.duration:.2f}s"
        )
        if self.failed > 0:
            error_map = {e["test"]: e["error"] for e in self.errors}
            for r in self.results:
                if r["success"] is False:
                    reason = error_map.get(r["test"]) or r.get("details", {}).get("error", "unknown")
                    line += f"\n  ✗ {r['test']}: {reason}"
        return line