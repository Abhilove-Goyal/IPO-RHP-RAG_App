"""Investor-first questions used to build a cross-IPO decision report."""

NON_NEGOTIABLE_QUESTIONS = [
    {
        "id": "business_industry",
        "title": "BUSINESS & INDUSTRY",
        "display_question": "What does the company do, what are its key products/segments, customers and markets, and what industry does it operate in?",
        "analysis_prompt": (
            "Summarize what the company does, its core business model, key products and operational segments, "
            "principal customers, geographical and target markets, and the industry in which it operates. "
            "Base every statement strictly on disclosed facts in the DRHP. If any detail (such as specific customer names "
            "or market share) is not disclosed, state that it is not disclosed."
        ),
        "target_metrics": ["company", "industry"],
    },
    {
        "id": "growth_competitive_position",
        "title": "GROWTH & COMPETITIVE POSITION",
        "display_question": "What are the company's key growth drivers, competitive strengths, major dependencies and industry-related opportunities?",
        "analysis_prompt": (
            "Assess the company's primary growth drivers, disclosed competitive strengths, major operational/commercial "
            "dependencies (such as key customers, top suppliers, critical partners, or facility concentration), and "
            "industry-related opportunities. Clearly distinguish factual disclosures from general industry claims or limitations."
        ),
        "target_metrics": ["major_growth_driver", "major_dependency_risk"],
    },
    {
        "id": "ipo_capital_structure",
        "title": "IPO & CAPITAL STRUCTURE",
        "display_question": "What is the IPO structure, including fresh issue, OFS, total offer, selling shareholders, pre/post-issue shareholding and potential dilution, and how could the IPO affect the company's capital structure?",
        "analysis_prompt": (
            "Extract the disclosed IPO structure with exact figures, currencies, and share units from relevant tables and disclosures where available. "
            "Specifically extract: fresh issue size (amount and share count), offer for sale (OFS) size (amount and share count), total offer size, "
            "named selling shareholders and shares offered by each, pre-issue and post-issue shareholding percentages, and potential dilution. "
            "Detail how the issue affects the company's equity capital and share capital structure. "
            "Request exact values from tables where available. If pricing, share counts, or post-issue metrics are placeholders or not yet determined, state that explicitly."
        ),
        "target_metrics": ["ipo_type", "fresh_issue", "ofs", "total_offer"],
        "prioritize_tables": True,
    },
    {
        "id": "shareholding_capital_history",
        "title": "SHAREHOLDING & CAPITAL HISTORY",
        "display_question": "How has the company's shareholding and equity capital evolved, including promoter holdings, promoter group, major shareholders, share issuances, transfers, bonuses, splits, preferential/private placements or other material capital events disclosed in the DRHP?",
        "analysis_prompt": (
            "Detail the evolution of the company's equity capital and shareholding based strictly on disclosed capital history tables and shareholding disclosures. "
            "Prioritize relevant tables and sections rather than relying only on narrative text. "
            "Extract exact values from tables where available for: promoter holdings and promoter group shareholding (number of shares and percentage), "
            "major/significant non-promoter shareholders, and material historical capital events including equity share issuances, share transfers, bonus issues, "
            "share splits, preferential allotments, rights issues, or private placements with dates, share counts, and issue prices where disclosed."
        ),
        "target_metrics": ["promoter_ownership"],
        "prioritize_tables": True,
    },
    {
        "id": "objectives_of_offer",
        "title": "OBJECTIVES OF THE OFFER",
        "display_question": "Why is the company raising money, how will the IPO proceeds be used, and do the stated objectives appear economically sensible based on the company's financial position and disclosed plans?",
        "analysis_prompt": (
            "Explain why the company is raising money and how the IPO proceeds will be utilized based strictly on the Objects of the Offer / Use of Proceeds tables and disclosures. "
            "Extract exact values from tables where available. Clearly distinguish fresh-issue proceeds (and their specific scheduled allocations such as capital expenditure, "
            "debt repayment/prepayment, working capital requirements, acquisitions, and general corporate purposes) from selling-shareholder OFS proceeds (which do not accrue to the company). "
            "Provide an objective analytical assessment of whether the stated objectives appear economically sensible based on the company's financial position, current borrowings, and disclosed plans."
        ),
        "target_metrics": [],
        "prioritize_tables": True,
    },
    {
        "id": "financial_performance_ratios",
        "title": "FINANCIAL PERFORMANCE & RATIOS",
        "display_question": "How strong are the company's financial performance, profitability, cash flows, balance sheet and key financial ratios, and what positive or negative trends should an investor notice?",
        "analysis_prompt": (
            "Analyze the company's disclosed financial performance, profitability, cash flows, balance sheet, and key financial ratios using exact figures from financial tables where available. "
            "Where disclosed or calculable from available table inputs, report: revenue from operations and revenue growth, EBITDA and EBITDA margin, PAT and PAT margin, "
            "EPS, Return on Net Worth / ROE, ROCE, debt/equity ratio, total borrowings/debt, interest coverage, operating cash flow, working-capital metrics (such as debtor/inventory/creditor days), "
            "and other material financial ratios disclosed in the DRHP. Highlight notable positive or negative trends across recent fiscal years/periods. "
            "Do NOT invent or calculate a ratio unless the required inputs are actually available in the evidence and the calculation is valid."
        ),
        "target_metrics": ["revenue", "ebitda_margin", "pat", "roe", "debt"],
        "prioritize_tables": True,
    },
    {
        "id": "material_risks",
        "title": "MATERIAL RISKS",
        "display_question": "What are the most important business, industry, financial, operational, regulatory and customer/supplier risks that an IPO investor should know first?",
        "analysis_prompt": (
            "Prioritize the most material risks disclosed in the DRHP that an IPO investor should know first. "
            "Group risks logically across: business and operational risks, industry and market risks, financial and indebtedness risks, "
            "regulatory and compliance risks, and customer or supplier concentration dependencies. "
            "Use cautious, objective risk-factor phrasing (e.g., 'may', 'could', 'is exposed to') and cite specific metrics or percentage dependencies disclosed in the evidence. "
            "Avoid generic speculation."
        ),
        "target_metrics": [],
    },
    {
        "id": "litigation_contingencies",
        "title": "MATERIAL LITIGATION & CONTINGENCIES",
        "display_question": "What material legal, regulatory, tax, criminal, civil, litigation, contingent-liability or other proceedings are disclosed, who is involved, what is the monetary exposure where available, and what could be the investor implication?",
        "analysis_prompt": (
            "Extract all disclosed material legal, regulatory, tax, criminal, civil, litigation, statutory penalties, and contingent liabilities involving the company, "
            "its promoters, directors, subsidiaries, or group companies. "
            "Provide a structured extraction covering: "
            "(1) matter type/category (e.g., direct tax, indirect tax, criminal, civil, regulatory), "
            "(2) entity/person involved, "
            "(3) case count or number of proceedings, "
            "(4) monetary exposure or claim amount where available (state clearly if unquantified), "
            "(5) current status of proceedings, "
            "(6) relevant dates or financial periods, and "
            "(7) potential investor implication. "
            "If no material litigation is disclosed or exposure is nil, state that explicitly."
        ),
        "target_metrics": [],
        "prioritize_tables": True,
    },
]
