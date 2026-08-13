"""Адаптеры источников (ТЗ §10).

`BY_SOURCE` — карта **generic observation collector**: эти адаптеры обязаны
превращать ответ прямо в ResearchObservation через общий pipeline. HIRING
устроен иначе: `trudvsem` сначала строит структуру вакансий и только затем
специализированный hiring-runtime выпускает SignalInput. Поэтому модуль
экспортируется, но в generic-карту не входит — иначе reach/collector пытался
бы трактовать VacancyDatum как обычное observation.

`rosstat_prices` пока решает только discovery актуального producer-price XLSX
для будущего SPREAD runtime. До появления workbook parser он также не входит в
`BY_SOURCE`: наличие файла ещё не означает готовое ResearchObservation.
"""

from . import cbr, fns, rosstat_prices, trudvsem

BY_SOURCE = {module.SOURCE_ID: module for module in (cbr, fns)}

__all__ = ["BY_SOURCE", "cbr", "fns", "rosstat_prices", "trudvsem"]
