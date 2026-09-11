-- 008 · Reference vocabulary
--
-- The normalised line items Phase 7/8 maps filing labels onto. Deliberately
-- small: these are the items needed for the v1 metric set (profitability,
-- leverage, growth, cash conversion). Valuation items are absent because
-- valuation needs prices, which Phase 3 put behind the private path.

INSERT INTO line_items (code, label, statement, is_flow, display_order) VALUES
    ('REVENUE',            'Revenue from operations',        'PL', true,   10),
    ('OTHER_INCOME',       'Other income',                   'PL', true,   20),
    ('OPERATING_EXPENSE',  'Total operating expenses',       'PL', true,   30),
    ('EBITDA',             'EBITDA',                         'PL', true,   40),
    ('DEPRECIATION',       'Depreciation and amortisation',  'PL', true,   50),
    ('FINANCE_COST',       'Finance costs',                  'PL', true,   60),
    ('PROFIT_BEFORE_TAX',  'Profit before tax',              'PL', true,   70),
    ('TAX_EXPENSE',        'Tax expense',                    'PL', true,   80),
    ('NET_PROFIT',         'Net profit after tax',           'PL', true,   90),
    ('EPS_BASIC',          'Basic earnings per share',       'PL', true,  100),

    ('SHARE_CAPITAL',      'Equity share capital',           'BS', false, 200),
    ('RESERVES',           'Other equity / reserves',        'BS', false, 210),
    ('TOTAL_EQUITY',       'Total equity',                   'BS', false, 220),
    ('BORROWINGS_LONG',    'Long-term borrowings',           'BS', false, 230),
    ('BORROWINGS_SHORT',   'Short-term borrowings',          'BS', false, 240),
    ('TOTAL_ASSETS',       'Total assets',                   'BS', false, 250),
    ('CASH',               'Cash and cash equivalents',      'BS', false, 260),
    ('INVENTORY',          'Inventories',                    'BS', false, 270),
    ('RECEIVABLES',        'Trade receivables',              'BS', false, 280),
    ('PAYABLES',           'Trade payables',                 'BS', false, 290),

    ('CF_OPERATING',       'Net cash from operating activities', 'CF', true, 300),
    ('CF_INVESTING',       'Net cash from investing activities', 'CF', true, 310),
    ('CF_FINANCING',       'Net cash from financing activities', 'CF', true, 320),
    ('CAPEX',              'Purchase of fixed assets',          'CF', true, 330)
ON CONFLICT (code) DO NOTHING;
