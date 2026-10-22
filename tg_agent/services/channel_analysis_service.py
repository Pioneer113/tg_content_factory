"""Channel rating service (#966): two-axis (usefulness × genre) classification.

Ports the *channel-level* classifier from the now-removed ``ai_detect_tool``
standalone seed (see #781) — the LLM judge prompt + verdict parser — plus the
lightweight ``emoji_trash_score`` feature. The per-message HeuristicAnalyzer
detector is intentionally NOT ported (it pulls heavy text features and risks XL
scope, per #966).

Reads/writes verdicts through ``db.repos.channel_ratings``. The LLM judge is
reached via a provider callable (``ProviderService.get_provider_callable()``),
so the service stays decoupled from any specific provider and is testable with a
fake callable.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Awaitable, Callable

from tg_agent.filters.criteria import AUTHORSHIP_KEYS
from tg_agent.models import ChannelGenre, ChannelRating, ChannelUsefulness
from tg_agent.utils.json import safe_json_loads_dict

if TYPE_CHECKING:
    from tg_agent.database.facade import Database

ProviderCallable = Callable[..., Awaitable[str]]

USEFULNESS_VALUES: tuple[ChannelUsefulness, ...] = ("useful", "useless")
GENRE_VALUES: tuple[ChannelGenre, ...] = ("ad", "infobiz", "aggregator", "copy", "original")

# Оси калибровки «полезное vs вода» (22.10.26). Судья = только калибровочный
# инструмент: его вердикты — ground truth для детерминированных эвристик
# (filters/usefulness.py), в проде LLM не участвует. Словарь ключей оси —
# канонический AUTHORSHIP_KEYS в criteria.py, общий с прод-эвристиками.

# Upper bound on the judge's free-text `reason`. The prompt asks for "кратко",
# but a buggy or hostile provider can echo a prompt-injection payload, paste an
# article, or run away — and `channel_ratings.reason` is plain TEXT (no DB-side
# limit) shown verbatim in the CLI / agent / web UI. Bound it at parse time so an
# unbounded blob never reaches the row or the screen (#1037).
MAX_REASON_LEN = 500

# Channel-title / post emoji detector (ported from the removed ai_detect_tool seed, #781).
_EMOJI_RE = re.compile(
    "[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff"
    "\U0001f1e0-\U0001f1ff\U00002702-\U000027b0\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f\U0001fa70-\U0001faff\U00002600-\U000026ff]"
)

_JUDGE_PROMPT = """Ты оцениваешь Telegram-канал по нескольким его постам. Дай независимые оценки.

ОСЬ 1 — ПОЛЕЗНОСТЬ (есть ли ценность для читателя, НЕЗАВИСИМО от того, написал человек или AI):
- useful: даёт читателю реальную пользу — конкретику, факты, разбор, рабочую информацию, экспертизу.
- useless: пустой контент ради контента — вода, общие банальности, накрутка объёма, инфоцыганские
  обещания без сути, бесполезные личные посты ни о чём, кликбейт без содержания.

ОСЬ 2 — ЖАНР (что это за канал по сути):
- ad: реклама товаров/услуг/недвижимости — цены, призывы купить/заказать/связаться, контакты.
- infobiz: инфобизнес — продажа курсов/марафонов/обучения/«успеха», вебинары, лид-магниты.
- aggregator: канал-КУРАТОР — собирает чужие источники СО СВОЕЙ подачей: дайджесты, подборки, ленты.
- copy: канал льёт чужое 1-в-1 БЕЗ своей подачи — голый репост/копипаста, клон чужого канала.
- original: канал производит СВОЙ авторский оригинальный контент — блог, авторские новости, обзоры.

ОСЬ 3 — ВОДА (по Ильяхову «Пиши, сокращай»): water_score 0-100 — сколько процентов текста
НЕ несёт смысла: штампы и канцелярит («в современном мире», «не секрет, что», «данный»),
вводные обороты-балласт, пустые фразы ради объёма. Конкретика (факты, цифры, код, выводы)
снижает оценку, пустословие — повышает.

ОСЬ 4 — АВТОРСКОСТЬ: authorship — из чего состоит контент канала, три доли в процентах
(сумма ≈ 100):
- own: свой опыт/мнение/анализ — автор пишет от первого лица о том, что делал, проверял,
  думал сам; личные наблюдения и выводы.
- quality_retell: пересказ чужих новостей С добавленной ценностью — автор добавляет
  контекст, анализ, сравнение, практические выводы «что это значит для меня».
- rewrite: рерайтинг/копипаста чужого БЕЗ добавленной ценности — пересказ ради пересказа,
  голые ссылки, безликие «стало известно» без своей мысли.

Посты канала:
{posts}

Ответь СТРОГО в JSON:
{{"useful": "useful|useless", "genre": "ad|infobiz|aggregator|copy|original",
"water_score": 0-100, "authorship": {{"own": 0-100, "quality_retell": 0-100, "rewrite": 0-100}},
"confidence": 0.0-1.0, "reason": "кратко"}}"""


def _parse_score_0_100(value: object) -> float | None:
    """Число 0-100 из ответа судьи; мусор/отсутствие → None (не поддельный ноль)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        score = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return min(max(score, 0.0), 100.0)


def _parse_authorship(value: object) -> dict[str, float] | None:
    """{own, quality_retell, rewrite} из ответа судьи; мусор/неполнота → None."""
    if not isinstance(value, dict):
        return None
    shares: dict[str, float] = {}
    for key in AUTHORSHIP_KEYS:
        share = _parse_score_0_100(value.get(key))
        if share is None:
            return None
        shares[key] = share
    return shares


class ChannelAnalysisService:
    def __init__(self, db: "Database") -> None:
        self._db = db

    # --- read -------------------------------------------------------------
    async def list_ratings(
        self,
        *,
        useful: str | None = None,
        genre: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ChannelRating]:
        return await self._db.repos.channel_ratings.list_ratings(
            useful=useful, genre=genre, limit=limit, offset=offset
        )

    async def get_rating(self, channel_id: int) -> ChannelRating | None:
        return await self._db.repos.channel_ratings.get(channel_id)

    # --- classify ---------------------------------------------------------
    async def sample_posts(self, channel_id: int, sample_size: int = 40) -> list[str]:
        """Public view of the posts the judge would see — lets callers cheaply
        pre-check for an empty channel before spending a provider call (#994)."""
        return await self._sample_posts(channel_id, sample_size)

    async def classify_channel(
        self,
        channel_id: int,
        *,
        provider_callable: ProviderCallable,
        sample_size: int = 40,
    ) -> ChannelRating:
        """Sample posts, ask the LLM judge, persist and return the verdict."""
        posts = await self._sample_posts(channel_id, sample_size)
        title, username = await self._channel_meta(channel_id)
        prompt = self._build_prompt(posts)
        # Расширенная рубрика (water_score + authorship) требует больше токенов,
        # чем старые две оси: JSON с вложенным объектом не влезает в 256.
        raw = await provider_callable(prompt=prompt, max_tokens=384, temperature=0.0)
        verdict = self._parse_verdict(raw)

        rating = ChannelRating(
            channel_id=channel_id,
            title=title,
            username=username,
            useful=verdict["useful"],
            genre=verdict["genre"],
            confidence=verdict["confidence"],
            reason=verdict["reason"],
            emoji_trash_score=self._emoji_trash_score(title, posts),
            n_total=len(posts),
            water_score=verdict["water_score"],
            authorship=verdict["authorship"],
        )
        await self._db.repos.channel_ratings.upsert(rating)
        return rating

    # --- helpers ----------------------------------------------------------
    @staticmethod
    def _build_prompt(posts: list[str]) -> str:
        joined = "\n---\n".join(p.strip() for p in posts if p and p.strip())
        return _JUDGE_PROMPT.format(posts=joined or "(нет постов)")

    @staticmethod
    def _parse_verdict(raw: str) -> dict:
        """Parse the judge JSON, with a lenient text-scan fallback (#966)."""
        # When the model didn't return clean JSON, fall through with an empty
        # dict and let the per-field text-scan fallbacks below recover the enums.
        parsed = safe_json_loads_dict(raw) or {}
        # Word-boundary match so short tokens don't match inside other words
        # (e.g. "ad" inside "broadcast"/"read") — review on #966.
        useful = parsed.get("useful")
        if useful not in USEFULNESS_VALUES:
            useful = next((v for v in USEFULNESS_VALUES if re.search(rf"\b{v}\b", raw)), "useless")
        genre = parsed.get("genre")
        if genre not in GENRE_VALUES:
            genre = next((g for g in GENRE_VALUES if re.search(rf"\b{g}\b", raw)), "original")
        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(max(confidence, 0.0), 1.0)
        reason = parsed.get("reason")
        if not isinstance(reason, str):
            reason = None
        elif len(reason) > MAX_REASON_LEN:
            # Keep the lead (the verdict gist usually comes first) and mark the cut.
            reason = reason[: MAX_REASON_LEN - 1].rstrip() + "…"
        return {
            "useful": useful,
            "genre": genre,
            "confidence": confidence,
            "reason": reason,
            # Оси калибровки: None = судья их не вернул (старая модель/мусор) —
            # честная «нет данных», не поддельный ноль.
            "water_score": _parse_score_0_100(parsed.get("water_score")),
            "authorship": _parse_authorship(parsed.get("authorship")),
        }

    @staticmethod
    def _emoji_trash_score(title: str | None, posts: list[str]) -> float:
        """Lightweight emoji-spam signal: post emoji density + title emoji count.

        Ported formula from the removed ai_detect_tool seed (#781; channel-level
        only — no per-message detector).
        """
        long_posts = [p for p in posts if p and len(p) >= 120]
        if long_posts:
            density = sum(
                len(_EMOJI_RE.findall(p)) / (len(p) / 100.0) for p in long_posts
            ) / len(long_posts)
        else:
            density = 0.0
        title_emojis = len(_EMOJI_RE.findall(title or ""))
        score = min(density / 2.0, 1.0) * 0.7 + min(title_emojis / 3.0, 1.0) * 0.3
        return round(score, 4)

    async def _channel_meta(self, channel_id: int) -> tuple[str | None, str | None]:
        channel = await self._db.repos.channels.get_channel_by_channel_id(channel_id)
        if channel is None:
            return None, None
        return channel.title, channel.username

    async def _sample_posts(self, channel_id: int, sample_size: int) -> list[str]:
        # Единый сэмпл с прод-рейтингом (fetch_recent_post_samples): судья и
        # эвристики должны оценивать ОДНУ популяцию постов, иначе калибровка
        # порогов пойдёт не по тому распределению.
        samples = await self._db.filter_repo.fetch_recent_post_samples(
            channel_id, sample_size=sample_size
        )
        return [text for text, _ in samples.get(channel_id, [])]
