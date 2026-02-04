"""
app_kernel.testing — Self-testing infrastructure for kernel-based apps.

Provides generic building blocks for functional tests that run against
the app's own HTTP endpoints, stream progress via SSE, and support
cancellation via the kernel's TaskStream system.

Kernel provides the harness; apps provide the tests.

Quick start — pass runner(s) to create_service:

    # my_app/main.py
    app = create_service(
        name="my_app",
        routers=[router],
        config=config,
        test_runners=[run_functional_tests],
    )
    # → auto-mounts POST /test/functional-tests (admin only, SSE, cancellable)

Runner signature:

    # my_app/test_runner.py
    from app_kernel.testing import TestReport, TestApiClient
    from app_kernel.tasks import TaskStream

    class MyClient(TestApiClient):
        async def create_widget(self, name):
            return await self.post("/widgets", json={"name": name})

    async def run_functional_tests(base_url: str, auth_token: str):
        stream = TaskStream("functional-test")
        yield stream.task_id_event()
        
        api = MyClient(base_url, auth_token, outer_task_id=stream.task_id)
        report = TestReport()
        
        result = await api.create_widget("test")
        report.add_result("create_widget", "id" in result)
        
        stream(report.summary_line())
        yield stream.log()
        yield stream.complete(report.all_passed, report=report.to_dict())
"""

from .report import TestReport
from .client import TestApiClient, consume_sse

__all__ = [
    "TestReport",
    "TestApiClient",
    "consume_sse",
]
