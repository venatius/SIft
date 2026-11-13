"""Optional versioned image features, leaving the core schema contract intact."""

from pathlib import Path
from uuid import uuid4

from . import database
from .image_analysis import VERSION
from .local_recognition import RECOGNITION_VERSION

PAGE_SIZE = 60
SCHEMA = """
CREATE TABLE IF NOT EXISTS image_features (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    input_size INTEGER NOT NULL,
    input_modified_ns INTEGER NOT NULL,
    input_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    thumbnail BLOB,
    dhash TEXT,
    red INTEGER, green INTEGER, blue INTEGER,
    width INTEGER, height INTEGER, low_detail INTEGER
)
"""


def installed(conn):
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_features'"
        ).fetchone()
        is not None
    )


def install(db_path):
    """Back up an existing core database before the first additive extension."""
    conn = database.connect(db_path)
    try:
        if not installed(conn):
            add_table(conn, db_path, SCHEMA)
        return conn
    except BaseException:
        conn.close()
        raise


def add_table(conn, db_path, schema):
    backup_path = Path(db_path).with_name(f"image-features-{uuid4().hex}.bak")
    backup = database.connect(backup_path)
    try:
        conn.backup(backup)
    finally:
        backup.close()
    conn.execute(schema)
    conn.commit()


# A cached image is valid only against the saved, successful current fingerprint.
CURRENT = """
    p.status = 'ok' AND p.algorithm = 'sha256' AND p.algorithm_version = '1'
    AND p.sha256 != '' AND p.input_size = f.size
    AND p.input_modified_ns = f.modified_ns AND f.status = 'ok'
"""
FEATURE_CURRENT = """
    v.version = ? AND v.input_size = f.size
    AND v.input_modified_ns = f.modified_ns AND v.input_sha256 = p.sha256
"""


def source_rows(conn, root_id=None):
    return conn.execute(
        """SELECT f.*, r.path AS root, p.sha256,
            COALESCE((SELECT m.orientation FROM media_metadata m
                      WHERE m.file_id = f.id AND m.status = 'ok'
                        AND m.input_size = f.size AND m.input_modified_ns = f.modified_ns
                      ORDER BY m.extracted_at DESC, m.id DESC LIMIT 1), 1) AS orientation
            FROM files f JOIN roots r ON r.id = f.root_id
            JOIN fingerprints p ON p.file_id = f.id
            WHERE """
        + CURRENT
        + " AND (? IS NULL OR f.root_id = ?) ORDER BY f.id",
        (root_id, root_id),
    )


def cached(conn, row):
    return (
        conn.execute(
            """SELECT 1 FROM image_features WHERE file_id = ? AND version = ?
        AND input_size = ? AND input_modified_ns = ? AND input_sha256 = ? AND status = 'ok'""",
            (row["id"], VERSION, row["size"], row["modified_ns"], row["sha256"]),
        ).fetchone()
        is not None
    )


def save(conn, row, features=None, error=None, status="failed"):
    values = (
        (None,) * 8
        if features is None
        else (
            features.thumbnail,
            features.dhash,
            features.red,
            features.green,
            features.blue,
            features.width,
            features.height,
            int(features.low_detail),
        )
    )
    conn.execute(
        """INSERT INTO image_features VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET
        version=excluded.version, input_size=excluded.input_size,
        input_modified_ns=excluded.input_modified_ns, input_sha256=excluded.input_sha256,
        status=excluded.status, error=excluded.error, thumbnail=excluded.thumbnail,
        dhash=excluded.dhash, red=excluded.red, green=excluded.green, blue=excluded.blue,
        width=excluded.width, height=excluded.height, low_detail=excluded.low_detail""",
        (
            row["id"],
            VERSION,
            row["size"],
            row["modified_ns"],
            row["sha256"],
            "ok" if features else status,
            error,
            *values,
        ),
    )
    conn.commit()


def roots(conn):
    return [dict(row) for row in conn.execute("SELECT id, path FROM roots ORDER BY path, id")]


def browse(conn, root_id=None, text="", status="all", after=0, category="all"):
    """A bounded stable ID page; queries never inspect source files."""
    if status not in {"all", "ready", "failed", "pending", "unsupported"}:
        raise ValueError("Unknown image status filter")
    feature_join = (
        "LEFT JOIN image_features v ON v.file_id = f.id"
        if installed(conn)
        else (
            "LEFT JOIN (SELECT NULL AS file_id, NULL AS version, NULL AS input_size, "
            "NULL AS input_modified_ns, NULL AS input_sha256, NULL AS status, "
            "NULL AS thumbnail, NULL AS error) v ON v.file_id = f.id"
        )
    )
    has_categories = categories_installed(conn)
    category_join = (
        "LEFT JOIN image_categories c ON c.file_id = f.id "
        "AND c.input_sha256 = p.sha256 AND c.preview_version = v.version "
        "AND c.recognition_version = ?"
        if has_categories
        else "LEFT JOIN (SELECT NULL AS category, NULL AS suggested, NULL AS uncertain) c ON 0"
    )
    query = (
        """WITH library AS (
        SELECT f.id, f.path, r.path AS root, f.size,
          CASE WHEN """
        + CURRENT
        + " AND "
        + FEATURE_CURRENT
        + """
            THEN CASE WHEN v.status = 'ok' THEN 'ready' ELSE v.status END
            ELSE 'pending' END AS image_status,
          CASE WHEN """
        + CURRENT
        + " AND "
        + FEATURE_CURRENT
        + """
            AND v.status = 'ok' THEN v.thumbnail ELSE NULL END AS thumbnail,
          v.error, c.category, c.suggested, c.uncertain
        FROM files f JOIN roots r ON r.id = f.root_id
        LEFT JOIN fingerprints p ON p.file_id = f.id
        """
        + feature_join
        + " "
        + category_join
        + """
        WHERE f.id > ? AND (? IS NULL OR f.root_id = ?)
          AND instr(lower(f.path), lower(?)) > 0
        ) SELECT * FROM library WHERE (? = 'all' OR image_status = ?)
        AND (? = 'all' OR (image_status = 'ready' AND
           (category = ? OR (? = 'review' AND category IN ('Screenshots', 'Possible clutter')))))
        ORDER BY id LIMIT ?"""
    )
    rows = [
        dict(row)
        for row in conn.execute(
            query,
            (
                VERSION,
                VERSION,
                *((RECOGNITION_VERSION,) if has_categories else ()),
                after,
                root_id,
                root_id,
                text,
                status,
                status,
                category,
                category,
                category,
                PAGE_SIZE + 1,
            ),
        )
    ]
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def categories_installed(conn):
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_categories'"
        ).fetchone()
        is not None
    )


CATEGORY_SCHEMA = """CREATE TABLE IF NOT EXISTS image_categories (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    input_sha256 TEXT NOT NULL,
    preview_version TEXT NOT NULL,
    recognition_version TEXT NOT NULL,
    category TEXT NOT NULL,
    suggested TEXT NOT NULL,
    uncertain INTEGER NOT NULL
)"""


def recognition_rows(conn, root_id):
    return conn.execute(
        """SELECT f.id, f.path, p.sha256, v.thumbnail FROM files f
        JOIN fingerprints p ON p.file_id = f.id
        JOIN image_features v ON v.file_id = f.id WHERE """
        + CURRENT
        + " AND "
        + FEATURE_CURRENT
        + """ AND v.status = 'ok' AND (? IS NULL OR f.root_id = ?)
        ORDER BY f.id""",
        (VERSION, root_id, root_id),
    )


def save_category(conn, row, result):
    conn.execute(
        """INSERT INTO image_categories VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET input_sha256=excluded.input_sha256,
        preview_version=excluded.preview_version,
        recognition_version=excluded.recognition_version, category=excluded.category,
        suggested=excluded.suggested, uncertain=excluded.uncertain""",
        (row["id"], row["sha256"], VERSION, RECOGNITION_VERSION, *result),
    )
    conn.commit()


def visual_rows(conn, root_id=None, file_id=None):
    if not installed(conn):
        return iter(())
    return conn.execute(
        """SELECT f.id, f.path, r.path AS root, p.sha256,
                  v.dhash, v.red, v.green, v.blue, v.width, v.height
           FROM files f JOIN roots r ON r.id = f.root_id
           JOIN fingerprints p ON p.file_id = f.id
           JOIN image_features v ON v.file_id = f.id
           WHERE """
        + CURRENT
        + " AND "
        + FEATURE_CURRENT
        + """
           AND v.status = 'ok' AND v.low_detail = 0
           AND (? IS NULL OR f.root_id = ?)
           AND (? IS NULL OR f.id = ?) ORDER BY f.path, f.id""",
        (VERSION, root_id, root_id, file_id, file_id),
    )
