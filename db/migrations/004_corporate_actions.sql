-- 004 · Corporate actions
--
-- Demoted by architecture §2.13, not removed. With no public prices there is
-- nothing to split-adjust. But companies restate PER-SHARE figures after a
-- split or bonus, so EPS and book value per share across a split boundary are
-- uninterpretable without knowing the action happened.
--
-- Sourced from statutory disclosures, not from a market data feed.

CREATE TABLE corporate_actions (
    action_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id   bigint      NOT NULL REFERENCES instruments (instrument_id),

    action_type     text        NOT NULL
        CHECK (action_type IN ('SPLIT', 'BONUS', 'DIVIDEND', 'RIGHTS',
                               'MERGER', 'NAME_CHANGE')),

    -- Valid time. announced_on matters separately from ex_date: a
    -- point-in-time view as of a date between the two must know the action was
    -- announced but not yet effective.
    announced_on    date        NOT NULL,
    ex_date         date,

    -- Ratio, for splits and bonuses. 1:5 split => from 1, to 5.
    ratio_from      numeric(12, 4) CHECK (ratio_from > 0),
    ratio_to        numeric(12, 4) CHECK (ratio_to   > 0),

    -- Per-share amount, for dividends.
    amount          numeric(20, 4) CHECK (amount >= 0),
    currency        char(3)     NOT NULL DEFAULT 'INR',

    known_from      timestamptz NOT NULL,

    source_id       text        NOT NULL REFERENCES sources (source_id),
    run_id          bigint      REFERENCES ingestion_runs (run_id),

    CONSTRAINT ratio_actions_have_a_ratio CHECK (
        action_type NOT IN ('SPLIT', 'BONUS')
        OR (ratio_from IS NOT NULL AND ratio_to IS NOT NULL)
    ),
    CONSTRAINT dividends_have_an_amount CHECK (
        action_type <> 'DIVIDEND' OR amount IS NOT NULL
    ),
    CONSTRAINT ex_date_not_before_announcement CHECK (
        ex_date IS NULL OR ex_date >= announced_on
    ),

    UNIQUE (instrument_id, action_type, announced_on, known_from)
);

CREATE INDEX idx_actions_asof
    ON corporate_actions (instrument_id, known_from DESC, ex_date);
