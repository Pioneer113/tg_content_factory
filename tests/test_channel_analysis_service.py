"""Tests for channel rating foundation (#966): seed, repo, and service."""

from __future__ import annotations

from tg_agent.models import ChannelRating
from tg_agent.services.channel_analysis_service import ChannelAnalysisService

# --- pure verdict parsing / scoring (no DB) -------------------------------


def test_parse_verdict_valid_json():
    raw = '{"useful": "useful", "genre": "original", "confidence": 0.82, "reason": "хорошо"}'
    v = ChannelAnalysisService._parse_verdict(raw)
    assert v == {
        "useful": "useful",
        "genre": "original",
        "confidence": 0.82,
        "reason": "хорошо",
        # Оси калибровки отсутствуют в старом формате ответа → честная «нет данных».
        "water_score": None,
        "authorship": None,
    }


def test_parse_verdict_calibration_axes_valid():
    raw = (
        '{"useful": "useful", "genre": "original", "water_score": 12, '
        '"authorship": {"own": 60, "quality_retell": 30, "rewrite": 10}, '
        '"confidence": 0.8, "reason": "авторский"}'
    )
    v = ChannelAnalysisService._parse_verdict(raw)
    assert v["water_score"] == 12.0
    assert v["authorship"] == {"own": 60.0, "quality_retell": 30.0, "rewrite": 10.0}


def test_parse_verdict_calibration_axes_missing_or_garbage():
    # Мусор в осях → None (не поддельный ноль) — рубрика судьи деградировала.
    raw = (
        '{"useful": "useless", "genre": "ad", "water_score": "много", '
        '"authorship": {"own": 60}, "confidence": 0.5, "reason": "r"}'
    )
    v = ChannelAnalysisService._parse_verdict(raw)
    assert v["water_score"] is None
    assert v["authorship"] is None

    # Неполный authorship (не все три доли) → None целиком, не частичный словарь.
    raw = '{"useful": "useless", "genre": "ad", "authorship": {"own": 60, "rewrite": 40}}'
    v = ChannelAnalysisService._parse_verdict(raw)
    assert v["authorship"] is None


def test_parse_verdict_water_score_clamped():
    v = ChannelAnalysisService._parse_verdict(
        '{"useful": "useful", "genre": "original", "water_score": 250}'
    )
    assert v["water_score"] == 100.0


def test_parse_verdict_text_fallback():
    # Not valid JSON, but contains the enum tokens.
    raw = "вердикт: useless канал, жанр infobiz, спам"
    v = ChannelAnalysisService._parse_verdict(raw)
    assert v["useful"] == "useless"
    assert v["genre"] == "infobiz"


def test_parse_verdict_defaults_and_clamps():
    v = ChannelAnalysisService._parse_verdict("garbage")
    assert v["useful"] == "useless"
    assert v["genre"] == "original"
    assert v["confidence"] == 0.0
    over = ChannelAnalysisService._parse_verdict('{"useful":"useful","genre":"ad","confidence":5}')
    assert over["confidence"] == 1.0


def test_emoji_trash_score():
    plain = ChannelAnalysisService._emoji_trash_score("Plain title", ["a" * 200])
    assert plain == 0.0
    spammy = ChannelAnalysisService._emoji_trash_score("🧠🔥🚀 Title", ["🔥" * 50 + "x" * 100])
    assert spammy > 0.0


# --- repo + seed over :memory: DB -----------------------------------------


async def test_csv_seed_loaded(db):
    count = await db.repos.channel_ratings.count()
    assert count == 447  # the committed seed CSV (#966)


async def test_repo_upsert_get_list(db):
    repo = db.repos.channel_ratings
    await repo.upsert(
        ChannelRating(channel_id=999001, useful="useful", genre="original", confidence=0.9)
    )
    got = await repo.get(999001)
    assert got is not None and got.useful == "useful" and got.confidence == 0.9

    # upsert again updates in place (no duplicate row).
    await repo.upsert(
        ChannelRating(channel_id=999001, useful="useless", genre="ad", confidence=0.5)
    )
    again = await repo.get(999001)
    assert again.useful == "useless" and again.genre == "ad"

    only_ads = await repo.list_ratings(genre="ad", limit=10)
    assert only_ads and all(r.genre == "ad" for r in only_ads)
    only_useless = await repo.list_ratings(useful="useless", genre="ad", limit=1000)
    assert any(r.channel_id == 999001 for r in only_useless)


async def test_repo_upsert_roundtrips_calibration_axes(db):
    """water_score + authorship живут в строке и читаются обратно (22.10.26)."""
    repo = db.repos.channel_ratings
    await repo.upsert(
        ChannelRating(
            channel_id=999004,
            useful="useful",
            genre="original",
            water_score=7.5,
            authorship={"own": 70.0, "quality_retell": 20.0, "rewrite": 10.0},
        )
    )
    got = await repo.get(999004)
    assert got is not None
    assert got.water_score == 7.5
    assert got.authorship == {"own": 70.0, "quality_retell": 20.0, "rewrite": 10.0}

    # Переклассификация без осей (старая рубрика) затирает их на NULL — последнее
    # суждение судьи и есть актуальное.
    await repo.upsert(
        ChannelRating(channel_id=999004, useful="useless", genre="ad")
    )
    got = await repo.get(999004)
    assert got is not None and got.water_score is None and got.authorship is None


async def test_classify_channel_persists_calibration_axes(db):
    """Расширенная рубрика доезжает до БД целиком (судья = калибровка, 22.10.26)."""

    async def fake_provider(*, prompt, max_tokens=256, temperature=0.0, **kw):
        assert "water_score" in prompt  # рубрика просит новые оси
        return (
            '{"useful": "useful", "genre": "original", "water_score": 8, '
            '"authorship": {"own": 80, "quality_retell": 15, "rewrite": 5}, '
            '"confidence": 0.9, "reason": "свой опыт"}'
        )

    svc = ChannelAnalysisService(db)
    rating = await svc.classify_channel(999005, provider_callable=fake_provider, sample_size=5)
    assert rating.water_score == 8.0
    assert rating.authorship is not None and rating.authorship["own"] == 80.0

    stored = await svc.get_rating(999005)
    assert stored is not None
    assert stored.water_score == 8.0
    assert stored.authorship is not None and stored.authorship["rewrite"] == 5.0


async def test_classify_channel_with_fake_provider(db):
    async def fake_provider(*, prompt, max_tokens=256, temperature=0.0, **kw):
        assert "Посты канала" in prompt
        return '{"useful": "useful", "genre": "aggregator", "confidence": 0.7, "reason": "ok"}'

    svc = ChannelAnalysisService(db)
    rating = await svc.classify_channel(999002, provider_callable=fake_provider, sample_size=5)
    assert rating.useful == "useful"
    assert rating.genre == "aggregator"
    assert rating.confidence == 0.7

    stored = await svc.get_rating(999002)
    assert stored is not None and stored.genre == "aggregator"


async def test_classify_channel_is_idempotent(db):
    """Re-running the judge on the same channel updates in place — no duplicate
    rows (#994 cycle-review: idempotency of the write path)."""
    calls = {"n": 0}

    async def fake_provider(*, prompt, max_tokens=256, temperature=0.0, **kw):
        calls["n"] += 1
        # Return a different verdict on the second run to prove update-in-place.
        if calls["n"] == 1:
            return '{"useful": "useful", "genre": "original", "confidence": 0.6, "reason": "first"}'
        return '{"useful": "useless", "genre": "ad", "confidence": 0.3, "reason": "second"}'

    svc = ChannelAnalysisService(db)
    before = await db.repos.channel_ratings.count()

    await svc.classify_channel(999003, provider_callable=fake_provider, sample_size=5)
    after_first = await db.repos.channel_ratings.count()
    assert after_first == before + 1

    await svc.classify_channel(999003, provider_callable=fake_provider, sample_size=5)
    after_second = await db.repos.channel_ratings.count()
    assert after_second == after_first  # no duplicate row

    stored = await svc.get_rating(999003)
    assert stored is not None and stored.useful == "useless" and stored.genre == "ad"


async def test_upsert_preserves_flag_count_on_reclassify(db):
    repo = db.repos.channel_ratings
    await repo.upsert(ChannelRating(channel_id=900100, useful="useful", genre="original", flag_count=3))
    # Re-classification builds the row with flag_count=0 (default) — must NOT reset.
    await repo.upsert(ChannelRating(channel_id=900100, useful="useless", genre="ad", flag_count=0))
    got = await repo.get(900100)
    assert got.flag_count == 3
    assert got.genre == "ad"  # other fields still update


def test_genre_fallback_word_boundary():
    # "ad" must not match inside "broadcast"; no genre token → default original.
    v = ChannelAnalysisService._parse_verdict("broadcast news digest, no json here")
    assert v["genre"] == "original"
    # whole-word genre token is matched
    v2 = ChannelAnalysisService._parse_verdict("это явная реклама, жанр ad точно")
    assert v2["genre"] == "ad"


# ---------------------------------------------------------------------------
# Adversarial LLM-judge hardening (#1037, epic #1024 tier-2).
#
# The judge is a single fallible LLM call (precedent: the binary AI-detector
# failed a blind human check with recall 0 → switch to channel-slop, see memory
# project_ai_detect_tool_verdict). These tests pin the *contract* of the parse /
# persist path against a hostile or buggy provider: clamp out-of-range
# confidence, bound an unbounded `reason`, and keep the independent
# emoji_trash_score signal honest so a human can review verdict disagreements
# (feedback_minimize_user_work).
# ---------------------------------------------------------------------------


def test_confidence_negative_extreme_is_clamped():
    """A provider returning a wildly negative confidence (-100.5) must clamp to
    the [0, 1] range, not leak a nonsensical score into the rating."""
    v = ChannelAnalysisService._parse_verdict(
        '{"useful": "useful", "genre": "ad", "confidence": -100.5, "reason": "x"}'
    )
    assert v["confidence"] == 0.0


def test_confidence_string_garbage_falls_back_to_zero():
    """Non-numeric confidence must not raise — it falls back to 0.0."""
    v = ChannelAnalysisService._parse_verdict(
        '{"useful": "useful", "genre": "ad", "confidence": "не число", "reason": "x"}'
    )
    assert v["confidence"] == 0.0


def test_long_reason_is_truncated_in_parse():
    """A judge that returns a multi-kilobyte `reason` (prompt-injection echo, a
    pasted article, a runaway model) must not push an unbounded blob into the
    rating row / UI. The parser bounds it to MAX_REASON_LEN (#1037)."""
    from tg_agent.services.channel_analysis_service import MAX_REASON_LEN

    long_reason = "спам " * 1000  # ~5000 chars, well over any sane limit
    raw = (
        '{"useful": "useless", "genre": "ad", "confidence": 0.9, '
        f'"reason": "{long_reason.strip()}"}}'
    )
    v = ChannelAnalysisService._parse_verdict(raw)
    assert v["reason"] is not None
    assert len(v["reason"]) <= MAX_REASON_LEN


def test_short_reason_is_left_intact():
    """Truncation must only kick in past the limit — normal short reasons pass
    through unchanged (regression guard for the truncation logic)."""
    v = ChannelAnalysisService._parse_verdict(
        '{"useful": "useful", "genre": "original", "confidence": 0.7, "reason": "ясно и кратко"}'
    )
    assert v["reason"] == "ясно и кратко"


# --- _emoji_trash_score boundary inputs -----------------------------------


def test_emoji_score_no_posts():
    """No posts at all → no density signal, title-only contribution."""
    assert ChannelAnalysisService._emoji_trash_score(None, []) == 0.0
    assert ChannelAnalysisService._emoji_trash_score("", []) == 0.0


def test_emoji_score_emoji_only_post_saturates():
    """A long post that is *only* emoji is maximum density; combined with an
    emoji-heavy title the score saturates near the 1.0 ceiling."""
    score = ChannelAnalysisService._emoji_trash_score("🔥🔥🔥 канал", ["🔥" * 200])
    assert 0.0 < score <= 1.0
    # Density alone (0.7 weight) already dominates a clean-title baseline.
    clean = ChannelAnalysisService._emoji_trash_score(None, ["🔥" * 200])
    assert score > clean


def test_emoji_score_ignores_short_posts():
    """Posts shorter than 120 chars carry no density signal (formula guard) —
    only the title contributes."""
    short_only = ChannelAnalysisService._emoji_trash_score(None, ["🔥🔥🔥 коротко"])
    assert short_only == 0.0
    with_title = ChannelAnalysisService._emoji_trash_score("🔥🔥🔥", ["🔥🔥🔥 коротко"])
    assert with_title > 0.0  # title-only contribution


async def test_classify_persists_truncated_reason(db):
    """End-to-end: a hostile provider returning a giant `reason` results in a
    persisted rating whose reason is bounded — the DB / UI never see the blob."""
    from tg_agent.services.channel_analysis_service import MAX_REASON_LEN

    giant = "врёт про пользу " * 500

    async def hostile_provider(*, prompt, max_tokens=256, temperature=0.0, **kw):
        return (
            '{"useful": "useful", "genre": "original", "confidence": 0.99, '
            f'"reason": "{giant.strip()}"}}'
        )

    svc = ChannelAnalysisService(db)
    rating = await svc.classify_channel(990501, provider_callable=hostile_provider, sample_size=5)
    assert rating.reason is not None and len(rating.reason) <= MAX_REASON_LEN

    stored = await svc.get_rating(990501)
    assert stored is not None and stored.reason is not None
    assert len(stored.reason) <= MAX_REASON_LEN


async def test_classify_trusts_judge_but_emoji_score_is_independent(db):
    """Adversarial recall: when the judge wrongly rates an obvious emoji-spam
    channel as 'useful', the service does NOT silently override the verdict (it
    trusts the judge), but the independent emoji_trash_score still flags the
    channel — this disagreement is exactly what a human reviewer triages
    (feedback_minimize_user_work). Without a second signal the false 'useful'
    would pass invisibly."""
    # Seed an emoji-spam channel: long emoji-dense posts. Сэмпл сэмплится
    # channels-driven — нужен row в channels, иначе сообщения не видны.
    await db.execute_write("INSERT OR IGNORE INTO channels (channel_id) VALUES (990601)")
    spam_post = "🔥🚀💰" * 60 + " купи срочно успей "
    for mid in range(1, 8):
        await db.execute_write(
            "INSERT INTO messages (channel_id, message_id, text, message_kind, date) "
            "VALUES (?, ?, ?, 'regular', '2026-01-01T00:00:00')",
            (990601, mid, spam_post),
        )

    async def lying_judge(*, prompt, max_tokens=256, temperature=0.0, **kw):
        # Judge confidently (and wrongly) calls obvious spam 'useful'/'original'.
        return '{"useful": "useful", "genre": "original", "confidence": 0.95, "reason": "топ"}'

    svc = ChannelAnalysisService(db)
    rating = await svc.classify_channel(990601, provider_callable=lying_judge, sample_size=10)

    # Contract: the LLM verdict is recorded verbatim (no hidden correction).
    assert rating.useful == "useful"
    assert rating.genre == "original"
    # But the independent heuristic disagrees loudly → reviewable signal.
    assert rating.emoji_trash_score is not None and rating.emoji_trash_score > 0.5
