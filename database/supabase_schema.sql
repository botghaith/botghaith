-- نفّذ هذا الملف مرة واحدة في Supabase → SQL Editor

CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT DEFAULT '',
    full_name TEXT DEFAULT '',
    points INTEGER DEFAULT 0,
    exams_taken INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exams (
    exam_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    questions JSONB NOT NULL,
    duration_minutes INTEGER DEFAULT 30,
    created_by BIGINT,
    is_active INTEGER DEFAULT 1,
    description TEXT DEFAULT '',
    expires_at TEXT,
    allow_multiple INTEGER DEFAULT 0,
    is_published INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS exam_results (
    id BIGSERIAL PRIMARY KEY,
    exam_id TEXT NOT NULL REFERENCES exams(exam_id),
    user_id BIGINT NOT NULL REFERENCES users(user_id),
    score REAL NOT NULL,
    total INTEGER NOT NULL,
    percentage REAL NOT NULL,
    answers JSONB,
    completed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS admin_questions (
    id BIGSERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    options JSONB NOT NULL,
    correct_index INTEGER NOT NULL,
    category TEXT DEFAULT 'general',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS activity_log (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    action TEXT,
    details TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS required_channels (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    title TEXT DEFAULT '',
    link TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exam_results_exam ON exam_results(exam_id);
CREATE INDEX IF NOT EXISTS idx_exam_results_user ON exam_results(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_log_created ON activity_log(created_at DESC);
