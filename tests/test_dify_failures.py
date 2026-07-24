from __future__ import annotations

import unittest

from core.dify_client import DifyClient


class DifyFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = DifyClient(
            api_key="test",
            base_url="https://api.dify.ai/v1",
            workflow_path="/workflows/run",
            response_mode="streaming",
            user="test",
            topic_input_key="topic",
        )

    def test_failed_workflow_is_not_normalized_as_empty_success(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "killed by timeout"):
            self.client._normalize_response(
                {
                    "event": "workflow_finished",
                    "data": {
                        "status": "failed",
                        "outputs": {},
                        "error": "PluginDaemonInternalServerError: killed by timeout",
                    },
                }
            )

    def test_successful_workflow_keeps_platform_outputs(self) -> None:
        result = self.client._normalize_response(
            {
                "data": {
                    "status": "succeeded",
                    "outputs": {
                        "zhihu": "知乎正文",
                        "csdn": "CSDN正文",
                        "sohu": "搜狐正文",
                        "cover_prompt": "business office",
                    },
                }
            }
        )
        self.assertEqual(result["zhihu"], "知乎正文")
        self.assertEqual(result["csdn"], "CSDN正文")
        self.assertEqual(result["sohu"], "搜狐正文")


if __name__ == "__main__":
    unittest.main()
