"""Investor-first questions used to build a cross-IPO decision report."""

NON_NEGOTIABLE_QUESTIONS = [
    {
        "id": "company_overview",
        "display_question": "What does the company do, and what are its main products, services, and markets?",
        "analysis_prompt": "Summarize the company's business model, principal products or services, operating markets, and key business segments. Use only disclosed information.",
    },
    {
        "id": "offer_structure",
        "display_question": "What is the IPO offer structure, including fresh issue, OFS, total offer, and selling shareholders?",
        "analysis_prompt": "Extract the disclosed IPO structure, including fresh issue size, offer for sale size, total offer size, share counts, and named selling shareholders. Clearly label any amount shown as a placeholder or not disclosed.",
    },
    {
        "id": "use_of_proceeds",
        "display_question": "How will the IPO proceeds be used?",
        "analysis_prompt": "Summarize each disclosed use of proceeds and distinguish fresh-issue proceeds from selling-shareholder proceeds. State when a use or amount is not disclosed.",
    },
    {
        "id": "financial_performance",
        "display_question": "What are the company’s recent financial performance and profitability trends?",
        "analysis_prompt": "Report disclosed revenue, profit or loss, margins, and important year-over-year trends. Preserve units, periods, and exact figures; do not calculate missing metrics.",
    },
    {
        "id": "growth_drivers",
        "display_question": "What are the company’s growth drivers and key dependencies?",
        "analysis_prompt": "Assess disclosed growth drivers, demand factors, customers, suppliers, partners, capacity, and concentration dependencies. Separate disclosed facts from limitations.",
    },
    {
        "id": "risk_factors",
        "display_question": "What are the most important risks an IPO investor should know first?",
        "analysis_prompt": "Prioritize material business, financial, operational, market, regulatory, legal, and execution risks disclosed in the DRHP. Avoid generic speculation.",
    },
    {
        "id": "promoters_governance",
        "display_question": "Who are the promoters, and what governance information should investors know?",
        "analysis_prompt": "Identify disclosed promoters and summarize relevant promoter holdings, management responsibilities, board and committee structure, related-party matters, and governance risks.",
    },
    {
        "id": "debt_liquidity",
        "display_question": "What are the company’s debt, cash flow, working-capital, and liquidity risks?",
        "analysis_prompt": "Summarize disclosed borrowings, repayment obligations, working-capital needs, cash flows, defaults, guarantees, and liquidity constraints. State when information is unavailable.",
    },
    {
        "id": "legal_regulatory",
        "display_question": "What material legal, regulatory, tax, and contingent-liability issues are disclosed?",
        "analysis_prompt": "Summarize material litigation, regulatory proceedings, tax matters, penalties, claims, and contingent liabilities disclosed in the filing.",
    },
    {
        "id": "valuation_disclosures",
        "display_question": "What IPO pricing and valuation information is disclosed, and what remains unavailable?",
        "analysis_prompt": "Report disclosed price-band, valuation, EPS, P/E, dilution, and comparable-company information. Do not infer valuation when the relevant price or metric is not disclosed.",
    },
]
