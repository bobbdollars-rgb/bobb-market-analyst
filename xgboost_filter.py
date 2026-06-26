"""
xgboost_filter.py — XGBoost Signal Filter
==========================================
Training dari trades.json (hasil trade nyata lo).
Predict probabilitas WIN sebelum signal di-fire.

Phase system:
  < 50 trades  → Model tidak ada / tidak reliable → AUTO-SKIP (rule-based only)
  50-199 trades → Model ditraining, threshold 0.60 (conservative)
  200+ trades   → Model matang, threshold 0.65 (optimal)

Integration di signal_engine._is_valid_setup():
  Rule-based APPROVE → XGBoost filter (kalau model ada) → FIRE / SKIP

Auto-retrain: setiap Sabtu bersamaan weekly report.
"""

from __future__ import annotations

import json
import os
import pickle
from typing import Optional, Tuple, List, Dict

import numpy as np

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "data")
TRADES_FILE = os.path.join(DATA_DIR, "trades.json")
MODEL_FILE  = os.path.join(DATA_DIR, "xgboost_model.pkl")
META_FILE   = os.path.join(DATA_DIR, "xgboost_meta.json")

MIN_TRADES_TO_TRAIN  = 50
MIN_TRADES_MATURE    = 200
THRESHOLD_EARLY      = 0.60
THRESHOLD_MATURE     = 0.65

FEATURE_COLS = [
    "htf_bias_aligned", "elliott_wave_clear", "fib_ote_or_prz",
    "chart_pattern_confirmed", "harmonic_prz_hit", "divergence_detected",
    "h4_choch_bos", "kill_zone_active", "fib_time_window_active",
    "lunar_cycle_aligned", "planetary_aspect_major",
    "score_total", "adx_h4", "rsi_m15", "rsi_h1",
    "astro_score", "lunar_phase_enc", "regime_enc",
    "signal_enc", "pair_group", "hour_wib",
]


# ════════════════════════════════════════════════════════════
# DATA LOADING
# ════════════════════════════════════════════════════════════

def _load_training_data() -> Tuple[Optional[np.ndarray], Optional[np.ndarray], int]:
    """
    Load closed trades from trades.json.
    Returns (X, y, n_trades) or (None, None, 0) if not enough data.
    WIN=1, LOSS=0. OPEN/EXPIRED excluded.
    """
    if not os.path.exists(TRADES_FILE):
        return None, None, 0

    with open(TRADES_FILE, "r") as f:
        trades = json.load(f)

    closed = [t for t in trades
              if t.get("status") in ("WIN", "LOSS")
              and t.get("features")]

    if len(closed) < MIN_TRADES_TO_TRAIN:
        print(f"[XGB] Only {len(closed)} closed trades — need {MIN_TRADES_TO_TRAIN} to train")
        return None, None, len(closed)

    X_rows, y_rows = [], []
    for t in closed:
        feats = t["features"]
        row = [feats.get(col, 0) for col in FEATURE_COLS]
        X_rows.append(row)
        y_rows.append(1 if t["status"] == "WIN" else 0)

    return np.array(X_rows, dtype=float), np.array(y_rows, dtype=int), len(closed)


# ════════════════════════════════════════════════════════════
# TRAINING
# ════════════════════════════════════════════════════════════

def train_model() -> dict:
    """
    Train XGBoost model dari trades.json.
    Saves model + metadata. Called every Saturday.
    Returns training report dict.
    """
    try:
        from xgboost import XGBClassifier
        from sklearn.model_selection import cross_val_score
    except ImportError:
        return {"success": False, "error": "xgboost/sklearn not installed",
                "note": "pip install xgboost scikit-learn"}

    X, y, n = _load_training_data()
    if X is None:
        return {"success": False, "error": f"Not enough data ({n} trades)", "n_trades": n}

    n_trades = len(y)
    win_rate = float(y.mean() * 100)
    threshold = THRESHOLD_MATURE if n_trades >= MIN_TRADES_MATURE else THRESHOLD_EARLY

    # XGBoost — chosen over LSTM per project notes (better with small tabular data)
    model = XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        use_label_encoder=False,
        random_state=42,
        verbosity=0,
    )

    # Cross-validation (3-fold to preserve temporal order)
    cv_scores = cross_val_score(model, X, y, cv=min(3, n_trades // 10),
                                scoring="roc_auc")
    cv_auc = float(cv_scores.mean())

    # Train on all data
    model.fit(X, y)

    # Feature importance
    importances = dict(zip(FEATURE_COLS, model.feature_importances_.tolist()))
    top_features = sorted(importances.items(), key=lambda x: -x[1])[:5]

    # Save model
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(MODEL_FILE, "wb") as f:
        pickle.dump(model, f)

    # Save metadata
    import datetime as dt
    meta = {
        "trained_at":   dt.datetime.utcnow().isoformat(),
        "n_trades":     n_trades,
        "win_rate_pct": round(win_rate, 1),
        "cv_auc":       round(cv_auc, 3),
        "threshold":    threshold,
        "top_features": top_features,
        "feature_cols": FEATURE_COLS,
    }
    with open(META_FILE, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"[XGB] Model trained: {n_trades} trades | WR {win_rate:.1f}% | CV-AUC {cv_auc:.3f}")
    return {"success": True, **meta}


# ════════════════════════════════════════════════════════════
# PREDICTION
# ════════════════════════════════════════════════════════════

def _load_model():
    """Load model from disk. Returns (model, meta) or (None, None)."""
    if not os.path.exists(MODEL_FILE) or not os.path.exists(META_FILE):
        return None, None
    try:
        with open(MODEL_FILE, "rb") as f:
            model = pickle.load(f)
        with open(META_FILE, "r") as f:
            meta = json.load(f)
        return model, meta
    except Exception as e:
        print(f"[XGB] Load error: {e}")
        return None, None


def predict(features: dict) -> Tuple[float, float, str]:
    """
    Predict WIN probability for a signal.
    Returns (probability, threshold, decision).
    decision: "APPROVE" | "SKIP" | "NO_MODEL"
    """
    model, meta = _load_model()
    if model is None:
        return 0.0, 0.0, "NO_MODEL"

    threshold = meta.get("threshold", THRESHOLD_EARLY)
    row = np.array([[features.get(col, 0) for col in FEATURE_COLS]], dtype=float)

    try:
        prob = float(model.predict_proba(row)[0][1])
        decision = "APPROVE" if prob >= threshold else "SKIP"
        return prob, threshold, decision
    except Exception as e:
        print(f"[XGB] Predict error: {e}")
        return 0.0, threshold, "NO_MODEL"


def is_model_ready() -> bool:
    return os.path.exists(MODEL_FILE) and os.path.exists(META_FILE)


def get_meta() -> Optional[dict]:
    if not os.path.exists(META_FILE):
        return None
    with open(META_FILE, "r") as f:
        return json.load(f)


# ════════════════════════════════════════════════════════════
# STATUS REPORT — for weekly report & Telegram
# ════════════════════════════════════════════════════════════

def model_status_text() -> str:
    """Returns human-readable model status for weekly report."""
    if not is_model_ready():
        # Count how many closed trades we have
        try:
            with open(TRADES_FILE, "r") as f:
                trades = json.load(f)
            n_closed = len([t for t in trades if t.get("status") in ("WIN","LOSS")])
        except Exception:
            n_closed = 0
        remaining = max(0, MIN_TRADES_TO_TRAIN - n_closed)
        return (f"🔴 XGBoost: Belum aktif ({n_closed}/{MIN_TRADES_TO_TRAIN} trades)"
                f" — butuh {remaining} trade lagi")

    meta = get_meta()
    n    = meta.get("n_trades", 0)
    auc  = meta.get("cv_auc", 0)
    wr   = meta.get("win_rate_pct", 0)
    thr  = meta.get("threshold", 0)
    top  = meta.get("top_features", [])[:3]
    top_str = ", ".join(f[0] for f in top)
    phase = "Mature ✅" if n >= MIN_TRADES_MATURE else "Early ⚠️"

    return (f"🤖 XGBoost: AKTIF ({phase})\n"
            f"   Training: {n} trades | WR {wr:.0f}% | AUC {auc:.2f}\n"
            f"   Threshold: {thr:.2f} | Top features: {top_str}")


# ════════════════════════════════════════════════════════════
# MAIN — train when called directly (from weekly report)
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("[XGB] Starting training...")
    result = train_model()
    if result["success"]:
        print(f"[XGB] SUCCESS — {result['n_trades']} trades, AUC {result['cv_auc']:.3f}")
        print(f"[XGB] Top features: {result['top_features'][:3]}")
    else:
        print(f"[XGB] FAILED — {result.get('error')}")
        if "note" in result:
            print(f"[XGB] Note: {result['note']}")
