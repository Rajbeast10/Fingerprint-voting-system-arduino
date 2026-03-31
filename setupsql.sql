-- ============================================================
-- Fingerprint Voting System — MySQL Database Setup Script
-- Run: mysql -u root -p < setup.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS fingerprint_voting
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE fingerprint_voting;

-- ── Voters Table ─────────────────────────────────────────────
-- Stores registered voter details + fingerprint binding
CREATE TABLE IF NOT EXISTS voters (
  id              INT           AUTO_INCREMENT PRIMARY KEY,
  name            VARCHAR(100)  NOT NULL,
  aadhar_number   VARCHAR(12)   NOT NULL UNIQUE,
  fingerprint_id  INT           NOT NULL UNIQUE,
  has_voted       BOOLEAN       NOT NULL DEFAULT FALSE,
  registered_at   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_aadhar (aadhar_number),
  INDEX idx_fingerprint (fingerprint_id)
) ENGINE=InnoDB;

-- ── Votes Table ──────────────────────────────────────────────
-- Stores candidates and their running vote totals
CREATE TABLE IF NOT EXISTS votes (
  id              INT           AUTO_INCREMENT PRIMARY KEY,
  candidate_name  VARCHAR(100)  NOT NULL UNIQUE,
  vote_count      INT           NOT NULL DEFAULT 0
) ENGINE=InnoDB;

-- ── Seed Candidates ──────────────────────────────────────────
INSERT INTO votes (candidate_name, vote_count) VALUES
  ('Candidate A', 0),
  ('Candidate B', 0),
  ('Candidate C', 0)
ON DUPLICATE KEY UPDATE vote_count = vote_count;  -- safe re-run

-- ── Vote Audit Log (optional but recommended) ────────────────
-- Keeps an immutable log without storing WHO voted for WHOM
CREATE TABLE IF NOT EXISTS vote_audit (
  id          INT         AUTO_INCREMENT PRIMARY KEY,
  voter_id    INT         NOT NULL,
  voted_at    TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (voter_id) REFERENCES voters(id) ON DELETE RESTRICT
) ENGINE=InnoDB;

-- ── Verify setup ─────────────────────────────────────────────
SELECT 'Database setup complete.' AS status;
SELECT table_name FROM information_schema.tables
  WHERE table_schema = 'fingerprint_voting';