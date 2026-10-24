"""The vocabulary of the debate outcome: the order of severities and outcomes, and their Russian names."""

from __future__ import annotations

import types

SEVERITY_ORDER = ("critical", "major", "minor", "none")
DROPPED = "dropped"
OUTCOME_ORDER = ("consensus", "confirmed", "restored", "refined", "disputed", "side", "unexamined", "question", DROPPED)
SEV_RANK = types.MappingProxyType({severity: rank for rank, severity in enumerate(SEVERITY_ORDER)})
OUT_RANK = types.MappingProxyType({outcome: rank for rank, outcome in enumerate(OUTCOME_ORDER)})
UNRANKED = 9
SET_ASIDE = ("dropped_by_synthesis", "rejected_by_verify", "immaterial")
SHIFTS = ("raised", "lowered", DROPPED)
RU_OUT = types.MappingProxyType({
    "consensus": "Консенсус, нашли обе стороны",
    "confirmed": "Подтвердила другая сторона",
    "restored": "Восстановлены после отсева",
    "refined": "Уточнены в обсуждении",
    "disputed": "Спорные",
    "side": "Найдены в споре",
    "question": "Открытые вопросы",
    "unexamined": "Не проверены другой стороной",
    DROPPED: "Сняты",
})
RU_SHIFT = types.MappingProxyType({"raised": "Повышено", "lowered": "Понижено", DROPPED: "Снято"})
RU_BY = types.MappingProxyType({
    "side": "по вердикту проверяющей стороны",
    "author": "уточнение автора, проверяющая сторона согласна",
    "moderator": "решение модератора",
})
