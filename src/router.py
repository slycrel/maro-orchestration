"""Phase 17: Behavior-Aligned Skill Router

Routes skill selection based on predicted execution success rather than
keyword similarity alone. Trained on execution-success signal from
memory/skill-stats.jsonl using TF-IDF features and logistic regression.

Falls back to keyword matching when fewer than MIN_TRAINING_SAMPLES examples
are available, or when sklearn is not installed.

Inspired by Memento-Skills (arXiv:2603.18743): InfoNCE offline RL router.
Practical approximation: logistic regression on TF-IDF features with
execution-success labels from skill-stats.jsonl.

Usage:
    from router import route_skills, train_router, maybe_retrain, get_router_stats
"""

from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from skills import Skill

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional ML imports
# ---------------------------------------------------------------------------

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score
    _SKLEARN_AVAILABLE = True
except ImportError:
    _SKLEARN_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    _ST_AVAILABLE = True
except ImportError:
    _ST_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_TRAINING_SAMPLES = 50    # below this, fall back to keyword matching
RETRAIN_EVERY_N = 50         # retrain when skill-stats grows by this many entries

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RouterStats:
    training_samples: int
    last_trained: Optional[str]
    holdout_accuracy: float      # 0.0–1.0
    model_path: str
    feature_method: str          # "tfidf" | "embeddings"
    min_samples_reached: bool    # True if >= MIN_TRAINING_SAMPLES

    def to_dict(self) -> dict:
        return {
            "training_samples": self.training_samples,
            "last_trained": self.last_trained,
            "holdout_accuracy": self.holdout_accuracy,
            "model_path": self.model_path,
            "feature_method": self.feature_method,
            "min_samples_reached": self.min_samples_reached,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RouterStats":
        return cls(
            training_samples=int(d.get("training_samples", 0)),
            last_trained=d.get("last_trained"),
            holdout_accuracy=float(d.get("holdout_accuracy", 0.0)),
            model_path=str(d.get("model_path", "")),
            feature_method=str(d.get("feature_method", "tfidf")),
            min_samples_reached=bool(d.get("min_samples_reached", False)),
        )


@dataclass
class RouteResult:
    skill_id: str
    skill_name: str
    score: float        # predicted success probability 0.0–1.0
    method: str         # "router" | "keyword" | "fallback"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _memory_dir() -> Path:
    from orch_items import memory_dir
    return memory_dir()


def _model_path() -> Path:
    return _memory_dir() / "router-model.pkl"


def _stats_path() -> Path:
    return _memory_dir() / "router-stats.json"


def _skill_stats_path(override: Optional[Path] = None) -> Path:
    if override is not None:
        return override
    return _memory_dir() / "skill-stats.jsonl"


def _skills_path() -> Path:
    return _memory_dir() / "skills.jsonl"


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def extract_features(text: str, vectorizer=None) -> List[float]:
    """Extract feature vector from text.

    Uses sentence-transformers (all-MiniLM-L6-v2) if available,
    otherwise TF-IDF with unigrams + bigrams.

    Args:
        text:       Input text to embed.
        vectorizer: Pre-fitted TfidfVectorizer for consistent transform.
                    If None and sklearn available, fits on [text] alone
                    (useful for single-item callers; prefer build_training_data
                    path for proper fit).

    Returns:
        List of floats (embedding or tfidf vector). Never raises.
    """
    try:
        if _ST_AVAILABLE:
            _model = SentenceTransformer("all-MiniLM-L6-v2")
            vec = _model.encode([text])[0]
            return list(float(v) for v in vec)
    except Exception:
        pass

    if _SKLEARN_AVAILABLE:
        try:
            if vectorizer is not None:
                mat = vectorizer.transform([text])
            else:
                vect = TfidfVectorizer(ngram_range=(1, 2), max_features=512)
                mat = vect.fit_transform([text])
            arr = mat.toarray()[0]
            return [float(v) for v in arr]
        except Exception:
            pass

    # Last resort: character-level hash features
    chars = text.lower()
    features = [float(chars.count(c)) for c in "abcdefghijklmnopqrstuvwxyz "]
    return features


# ---------------------------------------------------------------------------
# Training data assembly
# ---------------------------------------------------------------------------

def build_training_data(
    skill_stats_path: Optional[Path] = None,
) -> Tuple[List[str], List[float], List[str]]:
    """Build (X_texts, y_labels, skill_ids) from skill-stats and skills.

    Positive examples: success_rate > 0.6 → label 1.0
    Negative examples: success_rate < 0.4 → label 0.0
    Skips ambiguous middle range and skills with 0 uses.

    Returns:
        Tuple of parallel lists (texts, labels, skill_ids).
        Returns ([], [], []) if files are missing or empty.
    """
    stats_p = _skill_stats_path(skill_stats_path)
    skills_p = _skills_path()

    # Load skill stats
    stats_by_id: dict = {}
    if stats_p.exists():
        try:
            for line in stats_p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    sid = d.get("skill_id", "")
                    if sid:
                        stats_by_id[sid] = d
                except Exception:
                    continue
        except Exception:
            pass

    if not stats_by_id:
        return [], [], []

    # Load skill descriptions
    skills_by_id: dict = {}
    if skills_p.exists():
        try:
            for line in skills_p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    sid = d.get("id", "")
                    if sid:
                        skills_by_id[sid] = d
                except Exception:
                    continue
        except Exception:
            pass

    X_texts: List[str] = []
    y_labels: List[float] = []
    skill_ids: List[str] = []

    for sid, stat in stats_by_id.items():
        total_uses = int(stat.get("total_uses", 0))
        if total_uses == 0:
            continue

        success_rate = float(stat.get("success_rate", 1.0))

        # Only use clear positives and negatives
        if success_rate > 0.6:
            label = 1.0
        elif success_rate < 0.4:
            label = 0.0
        else:
            continue  # ambiguous — skip

        # Build feature text from skill description + trigger patterns
        skill_data = skills_by_id.get(sid, {})
        description = str(skill_data.get("description", stat.get("skill_name", sid)))
        trigger_patterns = skill_data.get("trigger_patterns", [])
        if isinstance(trigger_patterns, list):
            trigger_text = " ".join(str(p) for p in trigger_patterns)
        else:
            trigger_text = ""
        feature_text = f"{description} {trigger_text}".strip()

        X_texts.append(feature_text)
        y_labels.append(label)
        skill_ids.append(sid)

    return X_texts, y_labels, skill_ids


# ---------------------------------------------------------------------------
# Train
# ---------------------------------------------------------------------------

def train_router(
    skill_stats_path: Optional[Path] = None,
    model_path: Optional[Path] = None,
) -> RouterStats:
    """Train the behavior-aligned router from skill-stats.

    Returns RouterStats. If fewer than MIN_TRAINING_SAMPLES, returns
    RouterStats(min_samples_reached=False) without saving a model.
    """
    mp = model_path or _model_path()

    X_texts, y_labels, skill_ids = build_training_data(skill_stats_path)

    if not _SKLEARN_AVAILABLE:
        stats = RouterStats(
            training_samples=len(X_texts),
            last_trained=None,
            holdout_accuracy=0.0,
            model_path=str(mp),
            feature_method="tfidf",
            min_samples_reached=False,
        )
        _save_router_stats(stats)
        return stats

    if len(X_texts) < MIN_TRAINING_SAMPLES:
        stats = RouterStats(
            training_samples=len(X_texts),
            last_trained=None,
            holdout_accuracy=0.0,
            model_path=str(mp),
            feature_method="tfidf",
            min_samples_reached=False,
        )
        _save_router_stats(stats)
        return stats

    try:
        # Fit TF-IDF vectorizer
        vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=1024)
        X_tfidf = vectorizer.fit_transform(X_texts)

        # 80/20 split
        X_train, X_holdout, y_train, y_holdout = train_test_split(
            X_tfidf, y_labels, test_size=0.2, random_state=42
        )

        # Train logistic regression
        clf = LogisticRegression(max_iter=500, random_state=42)
        clf.fit(X_train, y_train)

        # Evaluate on holdout
        y_pred = clf.predict(X_holdout)
        holdout_acc = float(accuracy_score(y_holdout, y_pred))

        # Save model + vectorizer
        mp.parent.mkdir(parents=True, exist_ok=True)
        with mp.open("wb") as f:
            pickle.dump({"model": clf, "vectorizer": vectorizer}, f)

        now = datetime.now(timezone.utc).isoformat()
        stats = RouterStats(
            training_samples=len(X_texts),
            last_trained=now,
            holdout_accuracy=holdout_acc,
            model_path=str(mp),
            feature_method="tfidf",
            min_samples_reached=True,
        )
        _save_router_stats(stats)
        return stats

    except Exception as e:
        logger.warning("[router] train_router failed: %s", e)
        stats = RouterStats(
            training_samples=len(X_texts),
            last_trained=None,
            holdout_accuracy=0.0,
            model_path=str(mp),
            feature_method="tfidf",
            min_samples_reached=False,
        )
        _save_router_stats(stats)
        return stats


def _save_router_stats(stats: RouterStats) -> None:
    """Save RouterStats to memory/router-stats.json."""
    try:
        p = _stats_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(stats.to_dict(), indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[router] failed to save router stats: %s", e)


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_router() -> Tuple[Optional[object], Optional[object]]:
    """Load (model, vectorizer) from disk. Returns (None, None) on any error."""
    try:
        mp = _model_path()
        if not mp.exists():
            return None, None
        with mp.open("rb") as f:
            bundle = pickle.load(f)
        model = bundle.get("model")
        vectorizer = bundle.get("vectorizer")
        if model is None or vectorizer is None:
            return None, None
        return model, vectorizer
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def route_skills(
    goal: str,
    candidates: "List[Skill]",
    top_k: int = 3,
) -> List[RouteResult]:
    """Score candidate skills by predicted success probability.

    Args:
        goal:       Goal string (used as query context).
        candidates: List of Skill objects to score.
        top_k:      Maximum results to return.

    Returns:
        List of RouteResult sorted by score descending.
        method="router" if model was used, "keyword" if fallback.
    """
    if not candidates:
        return []

    model, vectorizer = load_router()

    if model is not None and vectorizer is not None:
        results: List[RouteResult] = []
        for skill in candidates:
            try:
                feature_text = skill.description or skill.name
                vec = vectorizer.transform([feature_text])
                proba = model.predict_proba(vec)[0]
                # Class 1 = success probability
                classes = list(model.classes_)
                if 1.0 in classes:
                    score = float(proba[classes.index(1.0)])
                elif 1 in classes:
                    score = float(proba[classes.index(1)])
                else:
                    score = float(max(proba))
                results.append(RouteResult(
                    skill_id=skill.id,
                    skill_name=skill.name,
                    score=score,
                    method="router",
                ))
            except Exception:
                results.append(RouteResult(
                    skill_id=skill.id,
                    skill_name=skill.name,
                    score=0.5,
                    method="keyword",
                ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    # Fallback: return as-is with neutral score
    return [
        RouteResult(
            skill_id=s.id,
            skill_name=s.name,
            score=0.5,
            method="keyword",
        )
        for s in candidates[:top_k]
    ]


# ---------------------------------------------------------------------------
# Maybe retrain
# ---------------------------------------------------------------------------

def maybe_retrain(force: bool = False) -> Optional[RouterStats]:
    """Retrain the router if enough new data has accumulated.

    Checks current skill-stats count against last training count.
    Retrains if (current - last) >= RETRAIN_EVERY_N or force=True.

    Returns:
        RouterStats if retrained, None if no retrain was needed.
    """
    # Count current skill-stats entries
    current_count = _count_skill_stats()

    if not force:
        last_stats = get_router_stats()
        last_count = last_stats.training_samples
        if (current_count - last_count) < RETRAIN_EVERY_N:
            return None

    return train_router()


def _count_skill_stats(skill_stats_path: Optional[Path] = None) -> int:
    """Count entries in skill-stats.jsonl."""
    p = _skill_stats_path(skill_stats_path)
    if not p.exists():
        return 0
    try:
        count = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                count += 1
        return count
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_router_stats() -> RouterStats:
    """Load RouterStats from disk or return a default zeroed stats object."""
    try:
        p = _stats_path()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return RouterStats.from_dict(d)
    except Exception:
        pass
    return RouterStats(
        training_samples=0,
        last_trained=None,
        holdout_accuracy=0.0,
        model_path=str(_model_path()),
        feature_method="tfidf",
        min_samples_reached=False,
    )
