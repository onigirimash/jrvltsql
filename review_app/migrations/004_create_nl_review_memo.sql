CREATE TABLE IF NOT EXISTS nl_review_memo (
    kettonum    VARCHAR(10) NOT NULL,
    memo        TEXT,
    flag        VARCHAR(10),
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (kettonum)
);
