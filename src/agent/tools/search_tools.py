# -*- coding: utf-8 -*-
"""
Search tools — wraps SearchService methods as agent-callable tools.

Tools:
- search_stock_news: search latest stock news
- search_comprehensive_intel: multi-dimensional intelligence search
"""

import logging
from typing import Any

from src.agent.tools.registry import ToolParameter, ToolDefinition, ToolPolicy

logger = logging.getLogger(__name__)

_NEWS_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read", "db_write_cache"],
    permissions=["news:read"],
    scope_dimensions=["stock"],
)
_INTEL_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read", "db_write_cache"],
    permissions=["intel:read"],
    scope_dimensions=["stock"],
)


def _get_db():
    """Lazy import for DatabaseManager."""
    from src.storage import get_db
    return get_db()


def _get_search_service():
    """Return shared SearchService singleton."""
    from src.search_service import get_search_service
    return get_search_service()


def _get_data_fetcher_manager():
    """Return the shared data-fetcher manager for zero-key A-share fallbacks."""
    from data_provider import DataFetcherManager

    return DataFetcherManager()


def _canonical_search_code(stock_code: str) -> str:
    from data_provider.base import canonical_stock_code, normalize_stock_code

    return canonical_stock_code(normalize_stock_code(str(stock_code or "").strip()))


def _plain_a_stock_code(stock_code: str) -> str:
    raw = str(stock_code or "").strip().upper()
    if "." in raw:
        raw = raw.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits.zfill(6) if digits and len(digits) <= 6 else digits


def _is_a_stock_code(stock_code: str) -> bool:
    code = _plain_a_stock_code(stock_code)
    return code.isdigit() and len(code) == 6


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


_RISK_KEYWORDS = (
    "减持",
    "被动减持",
    "处罚",
    "立案",
    "监管函",
    "问询函",
    "警示函",
    "调查",
    "诉讼",
    "仲裁",
    "冻结",
    "质押",
    "退市",
    "ST",
)
_EARNINGS_KEYWORDS = (
    "业绩",
    "业绩预告",
    "预增",
    "预亏",
    "扭亏",
    "年报",
    "半年报",
    "季报",
    "利润",
    "净利润",
    "营收",
    "营业收入",
)


def _row_text(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(key) or "") for key in ("title", "content", "type", "source"))


def _announcement_to_result(row: dict[str, Any]) -> dict:
    title = str(row.get("title") or "")
    source = str(row.get("source") or "a_stock_free_announcement")
    published_date = str(row.get("date") or "") or None
    pdf_url = str(row.get("pdf_url") or "")
    url = pdf_url or str(row.get("url") or "")
    snippet_parts = []
    if row.get("type"):
        snippet_parts.append(f"公告类型: {row.get('type')}")
    if pdf_url:
        snippet_parts.append(f"PDF: {pdf_url}")
    return {
        "title": title,
        "snippet": "；".join(snippet_parts) or title,
        "url": url,
        "source": source,
        "published_date": published_date,
    }


def _market_news_to_result(row: dict[str, Any]) -> dict:
    title = str(row.get("title") or "")
    content = str(row.get("content") or row.get("brief") or title)
    return {
        "title": title,
        "snippet": content[:500],
        "url": str(row.get("url") or ""),
        "source": str(row.get("source") or "a_stock_free_news"),
        "published_date": str(row.get("time") or "")[:10] or None,
    }


def _fund_flow_to_result(row: dict[str, Any], stock_name: str) -> dict:
    date = str(row.get("date") or "")
    net_amount = row.get("net_amount")
    close = row.get("close")
    turnover = row.get("turnover")
    title = f"{stock_name} 资金流向 fallback"
    snippet = f"日期={date}，收盘={close}，净额={net_amount}，换手={turnover}"
    return {
        "title": title,
        "snippet": snippet,
        "url": "",
        "source": "sina_fund_flow",
        "published_date": date or None,
    }


def _free_a_stock_news_results(stock_code: str, stock_name: str, *, limit: int = 5) -> list[dict]:
    """Return zero-key A-share announcement/news rows for Agent fallback."""
    if not _is_a_stock_code(stock_code):
        return []

    code = _plain_a_stock_code(stock_code)
    manager = _get_data_fetcher_manager()
    results: list[dict] = []

    announcements = manager.get_free_announcements(code, page_size=max(limit, 5))
    if not announcements:
        announcements = manager.get_fallback_announcements(code, page_size=max(limit, 5))
    for row in announcements:
        if row.get("title"):
            results.append(_announcement_to_result(row))
        if len(results) >= limit:
            return results

    # CLS is market-wide. Use it only as a broad fallback and prefer rows that
    # explicitly mention the target code/name.
    market_news = manager.get_free_market_news(page_size=30)
    matched = [
        row
        for row in market_news
        if stock_name and (stock_name in _row_text(row) or code in _row_text(row))
    ]
    for row in matched or market_news[: max(0, limit - len(results))]:
        if row.get("title"):
            results.append(_market_news_to_result(row))
        if len(results) >= limit:
            break
    return results


def _free_a_stock_dimension_results(
    stock_code: str,
    stock_name: str,
    *,
    dimension: str,
    limit: int = 3,
) -> list[dict]:
    if not _is_a_stock_code(stock_code):
        return []

    code = _plain_a_stock_code(stock_code)
    manager = _get_data_fetcher_manager()
    rows = manager.get_free_announcements(code, page_size=30)
    fallback_rows = manager.get_fallback_announcements(code, page_size=20)
    all_announcements = rows + [
        row for row in fallback_rows if row.get("title") not in {item.get("title") for item in rows}
    ]

    if dimension == "risk_check":
        filtered = [row for row in all_announcements if _contains_any(_row_text(row), _RISK_KEYWORDS)]
        return [_announcement_to_result(row) for row in filtered[:limit]]
    if dimension == "earnings":
        filtered = [row for row in all_announcements if _contains_any(_row_text(row), _EARNINGS_KEYWORDS)]
        return [_announcement_to_result(row) for row in filtered[:limit]]
    if dimension == "announcements":
        return [_announcement_to_result(row) for row in all_announcements[:limit]]
    if dimension == "latest_news":
        return _free_a_stock_news_results(code, stock_name, limit=limit)
    if dimension == "market_analysis":
        return [_announcement_to_result(row) for row in all_announcements[:limit]]
    if dimension == "fund_flow":
        return [
            _fund_flow_to_result(row, stock_name)
            for row in manager.get_fallback_fund_flow(code, days=10)[:limit]
        ]
    return []


def _build_free_a_stock_news_payload(stock_code: str, stock_name: str, *, reason: str) -> dict:
    results = _free_a_stock_news_results(stock_code, stock_name, limit=5)
    if not results:
        return {
            "query": f"{stock_name} {stock_code} a-stock-data fallback",
            "provider": "a-stock-data",
            "success": False,
            "error": f"SearchService unavailable and a-stock-data fallback returned no rows ({reason})",
        }
    return {
        "query": f"{stock_name} {stock_code} a-stock-data fallback",
        "provider": "a-stock-data",
        "success": True,
        "fallback_used": True,
        "fallback_reason": reason,
        "results_count": len(results),
        "results": results,
    }


def _build_free_a_stock_intel_payload(stock_code: str, stock_name: str, *, reason: str) -> dict:
    dimensions: dict[str, dict] = {}
    for dim_name in ("latest_news", "announcements", "risk_check", "earnings", "fund_flow"):
        rows = _free_a_stock_dimension_results(stock_code, stock_name, dimension=dim_name, limit=3)
        if rows:
            dimensions[dim_name] = {
                "query": f"{stock_name} {stock_code} {dim_name} a-stock-data fallback",
                "results_count": len(rows),
                "results": [
                    {
                        "title": row["title"],
                        "snippet": row["snippet"],
                        "source": row["source"],
                    }
                    for row in rows
                ],
            }

    if not dimensions:
        return {
            "error": f"Comprehensive intel search unavailable and a-stock-data fallback returned no rows ({reason})"
        }

    report_lines = [f"【{stock_name} a-stock-data 免费情报兜底】"]
    labels = {
        "latest_news": "最新消息",
        "announcements": "公司公告",
        "risk_check": "风险排查",
        "earnings": "业绩线索",
        "fund_flow": "资金流",
    }
    for dim_name, payload in dimensions.items():
        report_lines.append(f"\n{labels.get(dim_name, dim_name)}:")
        for item in payload["results"]:
            report_lines.append(f"- [{item['source']}] {item['title']}；{item['snippet']}")

    return {
        "report": "\n".join(report_lines),
        "dimensions": dimensions,
        "fallback_used": True,
        "fallback_provider": "a-stock-data",
        "fallback_reason": reason,
    }


def _response_has_results(response) -> bool:
    return bool(response and getattr(response, "success", False) and getattr(response, "results", None))


def _persist_news_response(
    *,
    stock_code: str,
    stock_name: str,
    dimension: str,
    response,
) -> None:
    """Best-effort news persistence for Agent search tools."""
    if not response or not getattr(response, "success", False) or not getattr(response, "results", None):
        return

    code = _canonical_search_code(stock_code)
    try:
        saved_count = _get_db().save_news_intel(
            code=code,
            name=stock_name,
            dimension=dimension,
            query=response.query,
            response=response,
            query_context=None,
        )
        logger.info(
            "Agent news intel persisted for %s (dimension=%s, new_records=%s)",
            code,
            dimension,
            saved_count,
        )
    except Exception as exc:
        logger.warning(
            "Agent news intel persistence failed for %s (dimension=%s): %s",
            code,
            dimension,
            exc,
        )


def _handle_search_stock_news(stock_code: str, stock_name: str) -> dict:
    """Search latest news for a stock."""
    service = _get_search_service()

    if not service.is_available:
        return _build_free_a_stock_news_payload(
            stock_code,
            stock_name,
            reason="search_service_unavailable",
        )

    response = service.search_stock_news(stock_code, stock_name, max_results=5)

    if not _response_has_results(response):
        fallback = _build_free_a_stock_news_payload(
            stock_code,
            stock_name,
            reason=response.error_message or "search_service_no_usable_results",
        )
        if fallback.get("success"):
            return fallback
        if not response.success:
            return {
                "query": response.query,
                "success": False,
                "error": response.error_message,
                "fallback_error": fallback.get("error"),
            }
        return fallback

    _persist_news_response(
        stock_code=stock_code,
        stock_name=stock_name,
        dimension="latest_news",
        response=response,
    )

    return {
        "query": response.query,
        "provider": response.provider,
        "success": True,
        "results_count": len(response.results),
        "results": [
            {
                "title": r.title,
                "snippet": r.snippet,
                "url": r.url,
                "source": r.source,
                "published_date": r.published_date,
            }
            for r in response.results
        ],
    }


search_stock_news_tool = ToolDefinition(
    name="search_stock_news",
    description="Search for the latest news articles about a specific stock. "
                "Requires both stock_code and stock_name for accurate search. "
                "Returns news titles, snippets, sources, and URLs.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="stock_name",
            type="string",
            description="Stock name in Chinese, e.g., '贵州茅台'",
        ),
    ],
    handler=_handle_search_stock_news,
    category="search",
    policy=_NEWS_READ_POLICY,
)


# ============================================================
# search_comprehensive_intel
# ============================================================

def _handle_search_comprehensive_intel(stock_code: str, stock_name: str) -> dict:
    """Multi-dimensional intelligence search."""
    service = _get_search_service()

    if not service.is_available:
        return _build_free_a_stock_intel_payload(
            stock_code,
            stock_name,
            reason="search_service_unavailable",
        )

    intel_results = service.search_comprehensive_intel(
        stock_code=stock_code,
        stock_name=stock_name,
        max_searches=6,
    )

    if not intel_results:
        return _build_free_a_stock_intel_payload(
            stock_code,
            stock_name,
            reason="search_service_no_dimensions",
        )

    # Format into readable report
    report = service.format_intel_report(intel_results, stock_name)

    # Also return structured data
    dimensions = {}
    for dim_name, response in intel_results.items():
        if response and response.success and getattr(response, "results", None):
            _persist_news_response(
                stock_code=stock_code,
                stock_name=stock_name,
                dimension=dim_name,
                response=response,
            )
            dimensions[dim_name] = {
                "query": response.query,
                "results_count": len(response.results),
                "results": [
                    {
                        "title": r.title,
                        "snippet": r.snippet,
                        "source": r.source,
                    }
                    for r in response.results[:3]  # limit to 3 per dimension to save tokens
                ],
            }

    if not dimensions:
        return _build_free_a_stock_intel_payload(
            stock_code,
            stock_name,
            reason="search_service_no_usable_results",
        )

    return {
        "report": report,
        "dimensions": dimensions,
    }


search_comprehensive_intel_tool = ToolDefinition(
    name="search_comprehensive_intel",
    description="Multi-dimensional intelligence search: latest news, market analysis, "
                "risk checking, earnings outlook, and industry trends for a stock. "
                "Returns a formatted report and structured results.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="stock_name",
            type="string",
            description="Stock name in Chinese, e.g., '贵州茅台'",
        ),
    ],
    handler=_handle_search_comprehensive_intel,
    category="search",
    policy=_INTEL_READ_POLICY,
)


ALL_SEARCH_TOOLS = [
    search_stock_news_tool,
    search_comprehensive_intel_tool,
]
