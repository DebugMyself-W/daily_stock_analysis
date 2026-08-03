# -*- coding: utf-8 -*-
"""Agent search tool fallback tests for zero-key A-share data sources."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent.tools import search_tools


class AgentSearchFreeFallbackTest(unittest.TestCase):
    def _manager(self) -> MagicMock:
        manager = MagicMock()
        manager.get_free_announcements.return_value = [
            {
                "title": "贵州茅台2026年半年度业绩预告",
                "type": "业绩预告",
                "date": "2026-07-30",
                "source": "cninfo",
                "url": "https://www.cninfo.com.cn/new/disclosure/detail?annoId=1",
                "pdf_url": "https://static.cninfo.com.cn/test.pdf",
            },
            {
                "title": "贵州茅台关于股东减持计划的公告",
                "type": "临时公告",
                "date": "2026-07-29",
                "source": "cninfo",
                "pdf_url": "https://static.cninfo.com.cn/risk.pdf",
            },
        ]
        manager.get_fallback_announcements.return_value = []
        manager.get_free_market_news.return_value = [
            {
                "title": "贵州茅台盘中消息",
                "content": "贵州茅台相关市场消息",
                "time": "2026-07-30 10:00:00",
                "source": "cls",
                "url": "https://www.cls.cn/detail/1",
            }
        ]
        manager.get_fallback_fund_flow.return_value = [
            {
                "date": "2026-07-30",
                "close": 1500.0,
                "net_amount": 12345.0,
                "turnover": 1.2,
            }
        ]
        return manager

    def test_search_stock_news_prefers_search_service_when_results_exist(self) -> None:
        result = SimpleNamespace(
            title="搜索结果",
            snippet="搜索摘要",
            url="https://example.com/news",
            source="search",
            published_date="2026-07-30",
        )
        response = SimpleNamespace(
            query="贵州茅台 600519 股票 最新消息",
            provider="SearchProvider",
            success=True,
            results=[result],
            error_message=None,
        )
        service = SimpleNamespace(is_available=True, search_stock_news=MagicMock(return_value=response))

        with (
            patch.object(search_tools, "_get_search_service", return_value=service),
            patch.object(search_tools, "_get_data_fetcher_manager") as get_manager,
            patch.object(search_tools, "_persist_news_response"),
        ):
            payload = search_tools._handle_search_stock_news("600519", "贵州茅台")

        self.assertTrue(payload["success"])
        self.assertEqual(payload["provider"], "SearchProvider")
        self.assertFalse(payload.get("fallback_used", False))
        get_manager.assert_not_called()

    def test_search_stock_news_uses_free_sources_when_search_unavailable(self) -> None:
        service = SimpleNamespace(is_available=False)

        with (
            patch.object(search_tools, "_get_search_service", return_value=service),
            patch.object(search_tools, "_get_data_fetcher_manager", return_value=self._manager()),
        ):
            payload = search_tools._handle_search_stock_news("600519", "贵州茅台")

        self.assertTrue(payload["success"])
        self.assertTrue(payload["fallback_used"])
        self.assertEqual(payload["provider"], "a-stock-data")
        self.assertGreaterEqual(payload["results_count"], 1)
        self.assertEqual(payload["results"][0]["source"], "cninfo")
        self.assertEqual(payload["results"][0]["url"], "https://static.cninfo.com.cn/test.pdf")

    def test_comprehensive_intel_uses_free_dimensions_when_search_returns_empty(self) -> None:
        service = SimpleNamespace(
            is_available=True,
            search_comprehensive_intel=MagicMock(return_value={}),
            format_intel_report=MagicMock(return_value=""),
        )

        with (
            patch.object(search_tools, "_get_search_service", return_value=service),
            patch.object(search_tools, "_get_data_fetcher_manager", return_value=self._manager()),
        ):
            payload = search_tools._handle_search_comprehensive_intel("600519", "贵州茅台")

        self.assertTrue(payload["fallback_used"])
        self.assertEqual(payload["fallback_provider"], "a-stock-data")
        self.assertIn("announcements", payload["dimensions"])
        self.assertIn("risk_check", payload["dimensions"])
        self.assertIn("earnings", payload["dimensions"])
        self.assertIn("fund_flow", payload["dimensions"])


if __name__ == "__main__":
    unittest.main()
