"""
app_kernel.testing — Self-testing infrastructure for kernel-based apps.

Provides generic building blocks for functional tests that run against
the app's own HTTP endpoints, stream progress via SSE, and support
cancellation via the kernel's TaskStream system.

Kernel provides the harness; apps provide the tests.

Quick start:

    # my_app/test_runner.py
    from app_kernel.testing import TestReport, TestApiClient
    from app_kernel.tasks import TaskStream

    class MyClient(TestApiClient):
        async def create_widget(self, name):
            return await self.post("/widgets", json={"name": name})

    async def run_functional_tests(base_url, auth_token, **kwargs):
        stream = TaskStream("functional-test")
        yield stream.task_id_event()
        
        api = MyClient(base_url, auth_token, outer_task_id=stream.task_id)
        report = TestReport()
        
        result = await api.create_widget("test")
        report.add_result("create_widget", "id" in result)
        
        stream(report.summary_line())
        yield stream.log()
        yield stream.complete(report.all_passed, report=report.to_dict())

    # my_app/routes/test.py
    from app_kernel.testing import create_test_router
    from ..test_runner import run_functional_tests

    router = create_test_router(
        runner_fn=run_functional_tests,
        required_env=["MY_API_KEY"],
    )
"""

from .report import TestReport
from .client import TestApiClient, consume_sse
from .router import create_test_router

__all__ = [
    "TestReport",
    "TestApiClient",
    "consume_sse",
    "create_test_router",
]
