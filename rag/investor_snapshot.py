"""Investor Snapshot / Infographic data structure.

Extracts supported structured facts from Decision Report questions without
making an additional LLM call. Never fabricates missing values.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


DISCLOSED_NEGATIVE_VALUES = {
    "",
    "none",
    "null",
    "nil",
    "n/a",
    "na",
    "not disclosed",
    "not available",
    "not specified",
    "unknown",
    "undisclosed",
    "placeholder",
    "insufficient disclosure",
}


@dataclass
class InvestorSnapshot:
    company: str | None = None
    industry: str | None = None
    ipo_type: str | None = None
    fresh_issue: str | None = None
    ofs: str | None = None
    total_offer: str | None = None
    promoter_ownership: str | None = None
    revenue: str | None = None
    ebitda_margin: str | None = None
    pat: str | None = None
    roe: str | None = None
    debt: str | None = None
    major_growth_driver: str | None = None
    major_dependency_risk: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_metric_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.strip().lower().rstrip(".:;,")
    if normalized in DISCLOSED_NEGATIVE_VALUES or "not disclosed" in normalized or "insufficient" in normalized:
        return None
    return text


def build_investor_snapshot(report: list[dict], document_id: str | None = None) -> dict[str, Any]:
    """
    Construct an Investor Snapshot data structure from the 8 report question results.
    
    Rules:
    - Only populate fields when supported by retrieved evidence in the question answers.
    - If a question failed (e.g. MODEL_ERROR, RATE_LIMIT) or evidence is missing/undisclosed,
      leave the corresponding field as None.
    - Never fabricate missing values.
    """
    snapshot = InvestorSnapshot()
    if not report:
        return snapshot.to_dict()

    questions_by_id = {
        q.get("id"): q
        for q in report
        if isinstance(q, dict) and q.get("id")
    }

    # 1. Business & Industry
    q1 = questions_by_id.get("business_industry")
    if q1 and q1.get("status") == "SUCCESS":
        metrics = q1.get("key_metrics") or {}
        snapshot.company = _clean_metric_value(metrics.get("company") or metrics.get("company_name"))
        snapshot.industry = _clean_metric_value(metrics.get("industry") or metrics.get("operating_industry"))

    # 2. Growth & Competitive Position
    q2 = questions_by_id.get("growth_competitive_position")
    if q2 and q2.get("status") == "SUCCESS":
        metrics = q2.get("key_metrics") or {}
        snapshot.major_growth_driver = _clean_metric_value(
            metrics.get("major_growth_driver") or metrics.get("growth_driver")
        )
        snapshot.major_dependency_risk = _clean_metric_value(
            metrics.get("major_dependency_risk") or metrics.get("dependency") or metrics.get("major_dependency")
        )

    # 3. IPO & Capital Structure
    q3 = questions_by_id.get("ipo_capital_structure")
    if q3 and q3.get("status") == "SUCCESS":
        metrics = q3.get("key_metrics") or {}
        snapshot.ipo_type = _clean_metric_value(metrics.get("ipo_type") or metrics.get("offer_type"))
        snapshot.fresh_issue = _clean_metric_value(metrics.get("fresh_issue") or metrics.get("fresh_issue_size"))
        snapshot.ofs = _clean_metric_value(metrics.get("ofs") or metrics.get("offer_for_sale") or metrics.get("ofs_size"))
        snapshot.total_offer = _clean_metric_value(metrics.get("total_offer") or metrics.get("total_offer_size"))

    # 4. Shareholding & Capital History
    q4 = questions_by_id.get("shareholding_capital_history")
    if q4 and q4.get("status") == "SUCCESS":
        metrics = q4.get("key_metrics") or {}
        snapshot.promoter_ownership = _clean_metric_value(
            metrics.get("promoter_ownership") or metrics.get("promoter_holding") or metrics.get("promoter_shareholding")
        )

    # 6. Financial Performance & Ratios
    q6 = questions_by_id.get("financial_performance_ratios")
    if q6 and q6.get("status") == "SUCCESS":
        metrics = q6.get("key_metrics") or {}
        snapshot.revenue = _clean_metric_value(metrics.get("revenue") or metrics.get("revenue_from_operations"))
        snapshot.ebitda_margin = _clean_metric_value(metrics.get("ebitda_margin") or metrics.get("ebitda"))
        snapshot.pat = _clean_metric_value(metrics.get("pat") or metrics.get("profit_after_tax"))
        snapshot.roe = _clean_metric_value(metrics.get("roe") or metrics.get("ronw") or metrics.get("return_on_equity"))
        snapshot.debt = _clean_metric_value(
            metrics.get("debt") or metrics.get("total_debt") or metrics.get("borrowings") or metrics.get("debt_equity")
        )

    return snapshot.to_dict()

