---
name: building-generator
description: Generate a whole building (levels, walls, doors/windows, floors, roofs, rooms) in Revit from a floor-plan image/PDF/sketch or a verbal description, by writing one structured JSON payload and calling the generate_building MCP tool, which builds everything inside Revit in a single transaction. This is the whole-building analog of the family generator — instead of one component, the JSON describes an entire building. Use when the user wants to turn a house/building plan into a Revit model, asks to "сгенерируй здание", "собери дом по плану", "построй дом/здание в Revit по картинке", "сделай генератор здания", or shows a plan and wants it reproduced as real Revit geometry rather than a single family.
---

# Генератор здания (JSON → Revit)

Логика: пользователь показывает план дома/здания (картинка, PDF, эскиз или
словесное описание) → Claude читает план и пишет **один** JSON по схеме ниже →
вызывается `generate_building`, который строит уровни, стены, двери/окна,
полы, кровлю и помещения **одной транзакцией** внутри Revit.

Инструмент: [tools/building_tools.py](../../../tools/building_tools.py)
(обёртка) + [revit_mcp/building.py](../../../revit_mcp/building.py) (маршрут,
исполняется внутри Revit). Полная документация полей — в докстринге
`generate_building` там же; эта схема продублирована здесь для офлайн-справки.

## Схема JSON (все длины и координаты — в метрах)

```json
{
  "levels": [{"name": "Level 1", "elevation": 0.0}],
  "walls": [
    {
      "id": "W1",
      "level": "Level 1",
      "start": {"x": 0.0, "y": 0.0},
      "end": {"x": 6.0, "y": 0.0},
      "height": 3.0,
      "type_name": "Generic - 200mm"
    }
  ],
  "openings": [
    {
      "host_wall": "W1",
      "kind": "door",
      "distance_along_wall": 1.5,
      "family_name": "Single-Flush",
      "type_name": "0915 x 2134mm",
      "sill_height": 0.0
    }
  ],
  "floors": [
    {"level": "Level 1", "type_name": "Generic 150mm",
     "boundary": [{"x":0,"y":0}, {"x":6,"y":0}, {"x":6,"y":4}, {"x":0,"y":4}]}
  ],
  "roofs": [
    {"level": "Level 2", "type_name": "Generic - 400mm",
     "boundary": [{"x":0,"y":0}, {"x":6,"y":0}, {"x":6,"y":4}, {"x":0,"y":4}]}
  ],
  "rooms": [
    {"level": "Level 1", "name": "Гостиная", "point": {"x": 3.0, "y": 2.0}}
  ]
}
```

- `walls[].id` — придумывается сам, нужен только чтобы привязать `openings`
  к конкретной стене (`host_wall`).
- `walls[].height` / `type_name` необязательны: высота по умолчанию — до
  следующего уровня выше (иначе 3 м), тип — первый доступный `WallType`.
- `openings[].distance_along_wall` — расстояние в метрах от **начала** стены
  (`start`), не от центра.
- `rooms[].point` обязан попасть внутрь замкнутого контура стен на этом
  уровне — иначе `NewRoom` ничего не создаёт, и запись уходит в `rooms_failed`.
- `roofs` — всегда плоская кровля по контуру (footprint), без уклона. Если
  нужен скат — генерировать плоскую, затем вручную задать уклон рёбер в Revit.

## Шаг 1. Прочитать план и собрать геометрию

Извлечь из плана: осевые/контуры стен как отрезки `start`→`end`, положения
проёмов вдоль стен, названия и точки помещений, этажность. Если план без
масштабной привязки — искать штамп с размерами или калибровать по известной
площади экспликации (как в `poliklinika-room-modules`), не гадать на глаз.

Замкнутость контура важна дважды: для `rooms` (иначе `NewRoom` промахнётся)
и визуально для кровли/пола (открытый контур даёт вырожденный `CurveLoop`,
элемент уйдёт в `*_failed`).

## Шаг 2. Узнать, что реально есть в целевой модели — до, а не после

`type_name`/`family_name` сопоставляются **по точному совпадению имени** с
тем, что уже загружено в модель. Несовпадение — не ошибка всего запроса,
просто эта стена/проём/пол/кровля молча падает в `*_failed`. Поэтому
**перед** тем как писать JSON, а не после первой неудачной попытки:

```
mcp__Revit_Connector__list_levels                       # точные имена уровней
mcp__Revit_Connector__list_family_categories             # какие категории семейств вообще есть
mcp__Revit_Connector__list_families (contains="Дверь")    # реальные family_name/type_name дверей
mcp__Revit_Connector__list_families (contains="Окно")     # то же для окон
```

Если названия стен/полов/кровли неизвестны — просто не указывать
`type_name`, тогда возьмётся первый доступный тип соответствующей категории
(достаточно для эскизной модели).

## Шаг 3. Собрать и вызвать один раз

Один вызов `generate_building` со всем зданием сразу (все уровни, все стены
всех этажей, все проёмы, полы, кровля, помещения) — не по одному элементу.

⚠ **Повторный вызов не дедуплицирует геометрию.** Уровни по имени
переиспользуются, но стены/полы/кровля/помещения создаются заново при каждом
вызове — повторный запуск с тем же JSON удвоит дом. Если нужно поправить
часть здания, использовать `modify_element`/`delete_elements` точечно, а не
перезапускать `generate_building` целиком.

## Шаг 4. Разобрать ответ

Ответ содержит `*_created` и `*_failed` по каждой категории — читать
`*_failed` и пересказывать пользователю причины (обычно: не найден уровень,
не найден `family_name`/`type_name`, точка помещения вне контура стен).
Частичный успех — нормальный исход, не повод считать вызов проваленным.

## Что проверить внутри Revit после первого запуска

Часть API (`RoofType`/`NewFootPrintRoof`, параметр `Sill Height` у
конкретных семейств окон) невозможно проверить вне живого Revit — обязательно
собрать пробный дом и посмотреть глазами, а не только по статусу `success`.
Отдельно проверить кириллицу в именах помещений/уровней — это именно то
место, где раньше ловились баги кодировки (см. `fix_mojibake_deep` /
`to_param_string` в `revit_mcp/utils.py`).

## Что навык не делает

- Не проектирует здание по нормативам (площади, зонирование, эвакуация) —
  это `poliklinika-zoning-check` для медицинских объектов; для прочих типов
  зданий нормоконтроль вне навыка, проверять вручную.
- Не строит скатную кровлю, лестницы, инженерные системы — только footprint-
  геометрия оболочки и планировки.
- Не читает растровые сканы лучше, чем позволяет обычное распознавание
  изображения — для точных обмеров по векторному PDF смотри
  `poliklinika-room-modules` (`scripts/extract_room_dims.py`).
