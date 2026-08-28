# ZM parity: Plans и Analytics

Аудитория: technical
Статус: контракт и реализация выполняются по проверяемым срезам
Дата наблюдения Android-приложения: 2026-08-25

## Цель

Скилл должен отвечать на те же финансовые вопросы, что разделы Plans и Analytics в ZenMoney, и использовать совпадающие правила периода, счетов, переводов, категорий и прогнозов. Внешний вид приложения воспроизводить не требуется.

## Источники

| Источник | Что подтверждает | Ограничение |
|---|---|---|
| [ZenMoney API](https://github.com/zenmoney/ZenPlugins/wiki/ZenMoney-API) | `Budget`, `Reminder`, `ReminderMarker`, `Transaction`, `Tag`, `Account.inBalance`, `Merchant`, `/v8/diff/`; `Budget.incomeLock/outcomeLock` задают, добавляются ли плановые операции к бюджету | Не описывает современную логику экранов Plans и Analytics |
| [Что такое баланс](https://support.zenmoney.ru/knowledge-bases/2/articles/299-chto-takoe-balans) | Балансовые счета участвуют в отчётах и бюджете; переход через границу баланса отражается как доход или расход | Не задаёт полную матрицу накоплений, кредитов и долгов |
| [Как лучше вести бюджет](https://support.zenmoney.ru/knowledge-bases/2/articles/49-kak-luchshe-vesti-byudzhet-v-dzen-mani) | Планируемые операции учитываются в бюджете | Не задаёт точную формулу совмещения budget и reminder |
| Android `ru.zenmoney.androidsub` 26.6, `versionCode=1093`, локаль `ru-RU` | Доступные настройки, разделы, отчёты и фильтры | Обезличенное наблюдение UI, а не публичный API-контракт; модель устройства не сохраняется |
| Статический аудит APK 26.6 (`SHA-256 6f458503…bd287c5`) | Enum режимов, поля настроек, основные арифметические выражения Plans и Analytics | APK обфусцирован R8; сложные selector-ветки считаются подтверждёнными только после contract test или UI oracle |
| [Google Play](https://play.google.com/store/apps/details?id=ru.zenmoney.androidsub) и [App Store](https://apps.apple.com/us/app/id905934786?l=ru) | Текущая продуктовая поверхность: планы, регулярные платежи, прогнозы и аналитика | Описания не фиксируют расчётные формулы |

## Наблюдаемая модель Plans

### Период

Приложение позволяет выбрать день начала периода. Для синтетических тестов используется период с 20-го числа по 19-е число следующего месяца. Это тестовый пример, а не опубликованная пользовательская настройка.

APK 26.6 подтверждает: `Budget.date` — календарный anchor `YYYY-MM-01` логического месяца. Клиент выбирает budgets по полуоткрытому календарному диапазону месяца, затем отображает их в billing-окне выбранного дня начала. Поэтому logical February с днём 31 сохраняет `Budget.date=YYYY-02-01`, хотя его фактическая граница начинается 1 марта.

Общий resolver использует полуоткрытый внутренний диапазон `[start, next_start)` и включительный публичный `end_date=next_start-1 day`. Если дня 29, 30 или 31 нет, граница переносится ровно на 1-е число следующего месяца, без clamp и без переноса лишних суток. Для недели `first_weekday` обязателен; месяц начинается 1-го, год — 1 января.

### Режим расчёта

В приложении доступны два режима:

- «Баланс vs Расходы»;
- «Доходы vs Расходы».

APK хранит `SmartBudgetBalanceMode` как `balance`, `budgetLimit`, `excludeOpeningBalance`. Современный UI сохраняет `EXCLUDE_OPENING_BALANCE` для «Доходы vs Расходы» и `BALANCE` при переключении обратно на «Баланс vs Расходы». Старый `BUDGET_LIMIT` имеет отдельную формулу и противоречивое поведение начального баланса, поэтому runtime возвращает явную unsupported-ошибку вместо нормализации.

`user.planSettings` приходит через обычный `/v8/diff/` как JSON-строка массива. Переключатели UI «учитывать» инвертированы: отсутствующий `EXCLUDE_*` включает направление. Storage default — `BALANCE` и пустой set; локальных guessed defaults больше нет.

| Периметр | Plans effect | Управляющая настройка |
|---|---|---|
| balance → balance | нейтральный, обе стороны сохранены | — |
| off-balance savings → balance | income по income-side | `EXCLUDE_TRANSFER_FROM_SAVINGS` |
| balance → off-balance savings | expense по outcome-side | `EXCLUDE_TRANSFER_TO_SAVINGS` |
| off-balance loan/credit card → balance | income по income-side | `EXCLUDE_TRANSFER_FROM_LOANS` |
| balance → off-balance loan/credit card | expense по outcome-side | `EXCLUDE_TRANSFER_TO_LOANS` |
| debt → balance | income по income-side | `EXCLUDE_TRANSFER_FROM_DEBTS` |
| balance → debt | expense по outcome-side | `EXCLUDE_TRANSFER_TO_DEBTS` |
| other off-balance → balance | income по income-side | `EXCLUDE_TRANSFER_FROM_OTHER_ACCOUNTS` |
| balance → other off-balance | expense по outcome-side | `EXCLUDE_TRANSFER_TO_OTHER_ACCOUNTS` |
| off-balance → off-balance | нет Plans effect, обе стороны сохранены | — |

Endpoint precedence совпадает с APK: debt; затем `type=loan` или `type=ccard && creditLimit>0`; затем `type=deposit` или `savings=true`; затем остальные известные собственные счета. Неизвестный endpoint не маскируется как other.

Подтверждённая формула дневного итога:

```text
totalExpenses = expense.fact + accumulated_or_projected_expenses
totalIncomes  = startBalance
              + income.fact
              + accumulated_or_projected_incomes
              + exchangeDifference.fact
balanceLeftover = totalIncomes - totalExpenses
```

`startBalance = 0` для plain `EXCLUDE_OPENING_BALANCE`; `INCLUDE_OPENING_BALANCE` включает его обратно. В `BALANCE` он включён режимом. Приложение берёт фактический баланс до начала месяца, а для будущего месяца — рекурсивный итог предыдущего дня. `BUDGET_LIMIT` остаётся unsupported из-за подтверждённого расхождения двух APK consumers.

### Разница по категории

В приложении доступны три политики:

1. считать разницу только для категорий с возвратами;
2. считать разницу для возвратов и доходно-расходных категорий;
3. не считать разницу.

Официальный API-контракт для budget lock:

- `incomeLock=true` — `income` задаёт точный доходный бюджет категории;
- `incomeLock=false` — доходный бюджет равен `income` плюс доходы по планируемым операциям в этом месяце по этой категории;
- `outcomeLock` работает так же для расходного бюджета.

APK подтверждает расчёт строки Plans. Обозначения: `B` — сохранённый `Budget.outcome`, `P` — ещё плановые markers, `Q` — обработанные плановые операции, `A_refund` — факт с применённой политикой возвратов.

```text
B* = B                 при outcomeLock=true
B* = B + P + Q         при outcomeLock=false
R  = max(0, P, B* - A_refund)
R  = 0                 при abs(B*) < 0.01
rowTotal = fact + R
```

Поэтому при `fact=250`, `B=200`, `P=100`, `Q=0` unlocked budget field равен `300`, остаточный резерв равен `100`, а нагрузка Plans `fact + R` равна `350`. Эти поля нельзя называть одним `budget total`.

Факт, план и обработанный план поднимаются к родителям. Бюджеты детей поднимаются только через unlocked-родителя; locked-родитель сохраняет собственный `B`. Остаток дерева считается postorder: `R_final(node) = max(R_self(node), sum(R_final(children)))`. Итог берёт только корневые категории. Агрегат `ALL` не добавляется поверх них в Plans.

Публичная строка категории отделяет экранные значения от внутреннего резерва дерева:

- `plan = effective_budget` — знаменатель строки «из ...»;
- `remaining = max(plan - fact, 0)` — сколько свободно по строке;
- `overspend = max(fact - plan, 0)` — перерасход;
- `reserve_remaining = R_final` — внутренний остаточный резерв, который участвует в итоговой нагрузке Plans.

У родительской категории `remaining` и `reserve_remaining` могут различаться: расход в дочерней категории без собственного плана уменьшает экранный свободный остаток родителя, но не поглощает резерв другой дочерней категории. Raw `budget`, reminder-компоненты и `effective_budget` не подменяют друг друга.

Для доходной стороны те же технические поля симметричны: `overspend` означает превышение фактического дохода над планом, а не перерасход денег.

Политики приложения соответствуют enum `REFUNDS`, `INCOME_OUTCOME_AND_REFUNDS`, `NONE`; сохранённый default — `REFUNDS`, а balance-mode принудительно использует `NONE`.

Для `REFUNDS` APK использует только основную категорию операции. Противоположная операция вычитается как возврат, только если `incomeAccount == outcomeAccount` и видимость категории односторонняя: `showOutcome=true, showIncome=false` для возврата расхода либо обратное сочетание для возврата дохода. Операция между разными счетами остаётся обычным фактом, а категория, видимая с обеих сторон, не получает refund-netting. Эти поля приходят в API как `Tag.showIncome` и `Tag.showOutcome`.

Для `INCOME_OUTCOME_AND_REFUNDS` после построения дерева категорий вычисляется `delta = income - expense`; знак оставляет разницу на доходной или расходной стороне. Компенсационная поправка на собственном значении родителя сохраняет точный post-tree итог без двойного счёта детей. `NONE` сохраняет обе raw-стороны.

### Прогноз

APK подтверждает пользовательский переключатель `user.isForecastEnabled` и отдельные признаки прогноза у marker и сторон бюджета. При выключенном прогнозе Plans исключает `reminderMarker.isForecast=true`, обнуляет только помеченную `budget.isIncomeForecast` или `budget.isOutcomeForecast` сторону бюджета и сохраняет вторую сторону той же строки. При включённом прогнозе эти markers и суммы бюджета участвуют в расчёте. Если поле `user.isForecastEnabled` отсутствует в старом кеше, применяется APK-default `true`.

Контракт первой версии:

- обычный будущий marker учитывается один раз;
- `isForecast=true` marker не входит в Plans summary, категории, календарь и дневной forecast только при `user.isForecastEnabled=false`;
- `isIncomeForecast` и `isOutcomeForecast` обнуляют при выключенном прогнозе только соответствующую сторону бюджета;
- `processed` marker с точной связью `transaction.reminderMarker` не дублирует созданную по нему транзакцию;
- в `income_vs_expense` несвязанный `processed` marker одинакового по валюте перевода между балансовыми счетами временно даёт исходящий текущий факт; после появления точной связи факт принадлежит транзакции. Сопоставление по сумме, дате или счетам запрещено;
- `deleted` и прошлые markers не входят в прогноз;
- отключение календарного вывода не отключает сам расчёт прогноза.

Горизонт прогноза требует отдельного подтверждения. До этого включённый прогноз не считается готовым к реализации.

### Содержимое экрана

На экране Plans подтверждены:

- график периода;
- карточка дефицита или свободного остатка;
- секции поступлений и расходов;
- вложенные категории;
- плановые шкалы категорий;
- плановые платежи.

Каждый показатель должен быть сопоставлен с API-сущностью или помечен как вычисляемое поведение приложения. Если форма показателя известна, а формула нет, скилл возвращает структурированную ошибку `UNSUPPORTED_CALCULATION`; он не выдаёт приблизительное значение под тем же названием.

## Наблюдаемая модель Analytics

В списке Analytics подтверждены отчёты. Таблица задаёт границы реализации, а не только inventory:

| Отчёт | Входы | Метрика и выход | Валюта и фильтры | Реализация |
|---|---|---|---|---|
| Доходы vs расходы | период, счета, категории, policy разницы | отдельные отчёты `income`, `outcome`, `net`; итог по валюте | `currency_mode=split`; без неявной конвертации | `get_analytics`; первый срез |
| Расходы по категориям | период, счета, primary/additional categories | дерево категорий и `outcome` | `currency_mode=split`; primary tag группирует, additional tags фильтруют | `get_analytics(report=outcome, group_by=category, currency_mode=split)` |
| Расходы по магазинам | период, счета, места | `outcome` по merchant/payee | `currency_mode=split` | `get_analytics(report=outcome, group_by=merchant, currency_mode=split)` |
| Доходы по категориям | период, счета, категории | дерево категорий и `income` | `currency_mode=split` | `get_analytics(report=income, group_by=category, currency_mode=split)` |
| Сравнение периодов | текущий и до 12 предыдущих period resolver outputs | income, outcome, residue и delta | историческая конвертация в основную валюту | `get_income_outcome_comparison` |
| Тренды расходов | последовательность периодов | `last-first`, процент к `abs(first)`, fallback при нуле | одна series на валюту и scope | отдельный report tool |
| Динамика баланса | дата/период, scope счетов | восстановленная balance series и направление тренда | основная или явно выбранная валюта, курс на дату точки | `get_balance_trend` |
| Движение денег | период, счета | inflow, outflow, residue/overspending, weights | native-currency buckets | `get_money_flow`; не часть `get_analytics` |
| Plans | billing period и режим Plans | подтверждённый `balanceLeftover` и category reserve | политика Plans | `analyze_budget_detailed` |
| Category report | период, направление, TAG/PAYEE, policy разницы | actual, `BUDGET` или исторический `MEAN`, comparison | историческая конвертация в основную валюту | `get_category_report` |

Наблюдаемые фильтры включают:

- анализируемый поток: доходы или расходы;
- группировку по категории или месту;
- неделю, месяц, год и произвольный период;
- область счетов;
- область категорий;
- дополнительные категории;
- политику разницы по категориям;
- места и магазины.

`net` и движение денег — разные контракты. `get_analytics` сохраняет native-currency split и не выполняет неявную конвертацию; `get_money_flow` также возвращает отдельные native-currency buckets. Category report, comparison и balance trend, напротив, явно строят одну series: они запрашивают исторический курс на дату операции/точки и используют текущий `Instrument.rate` только как fallback. `currency_mode=scalar` у базового `get_analytics` завершается `MIXED_CURRENCY`, если в scope больше одной валюты.

Четыре advanced tools используют тот же строгий resolver периода и `account_scope=all|in_balance|selected`. Category report применяет `BUDGET` либо `MEAN` и точную category-difference policy. Comparison поддерживает `WHOLE_PERIOD`; `AVERAGE_VALUES` для диапазона длиннее 31 дня возвращает `UNSUPPORTED_CALCULATION`, потому что APK-формула длинного усреднения не подтверждена. Balance trend восстанавливает исторические holdings из текущих остатков и синхронизированных транзакций, поэтому источник явно указан в metadata.

Строгий контракт `get_analytics`:

- `report` обязателен и принимает только `income`, `outcome`, `net`;
- `group_by` опционален: по умолчанию `category`; принимает `category`, `account`, `merchant`;
- `currency_mode` опционален: по умолчанию `split`; принимает `split` или `scalar`;
- `account_scope` опционален: по умолчанию `in_balance`; принимает `all`, `in_balance`, `selected`;
- `category_scope` опционален: по умолчанию `all`; принимает `all`, `selected`;
- `category_role` принимает `primary`, `additional`, `any` и разрешён только при `category_scope=selected`;
- `merchant_scope` опционален: по умолчанию `all`; принимает `all`, `selected`;
- `account_ids`, `category_ids`, `merchant_ids`, `payees` используются только во множественной форме и только для `selected`-фильтров;
- `currency_mode=scalar` разрешён только для одной валюты, иначе возвращает `MIXED_CURRENCY`;
- поля ответа используют `snake_case`;
- стабильные ключи групп имеют префиксы `category:`, `account:`, `merchant:`; при группировке `merchant` операция без merchant ID использует запасной ключ `payee:`;
- `tag_policy=primary_tag`: первая категория — ключ группировки, дополнительные теги не размножают сумму по группам;
- `currency_conversion=none`: валюта берётся сначала из соответствующей стороны транзакции, затем из инструмента счёта;
- `unknown_currency=separate_bucket`: неизвестная валюта получает отдельный bucket, а не `RUB`;
- `transfers=excluded`: переводы остаются исключены из этого среза до подтверждения shared classifier.

Нормативный контракт фильтров:

- фильтр счёта применяется к стороне операции, соответствующей `report`; `income` фильтрует income-side account, `outcome` фильтрует outcome-side account, `net` применяет report-side правило к обеим сторонам, участвующим в расчёте;
- `account_scope=all` не фильтрует по `Account.inBalance` и сохраняет операции с неизвестным счётом в группе `account:unknown`;
- `account_scope=in_balance` включает только аккаунты с `inBalance=true`; аккаунты с отсутствующим `inBalance` исключаются;
- `account_scope=selected` требует непустой `account_ids`;
- архивный счёт не исключается сам по себе: он участвует, если соответствует выбранному `account_scope`;
- `category_scope=all` не фильтрует по тегам;
- `category_scope=selected` требует непустой `category_ids`; `category_role` задаёт, где искать тег: `primary`, `additional` или `any`;
- `category_role` без `category_scope=selected` невалиден;
- группировка по категориям всегда строится по первому тегу; дополнительные теги не создают групп;
- неизвестный tag ID сохраняется как `category:<id>` с именем `Unknown Category`; только пустой список тегов даёт `category:uncategorized`;
- `merchant_scope=all` не фильтрует по merchant/payee;
- `merchant_scope=selected` требует хотя бы один непустой фильтр `merchant_ids` или `payees`;
- если у операции есть merchant ID, он имеет приоритет над `payee`; `payee` используется только при отсутствии merchant ID;
- `payees` сравниваются после NFC-нормализации точным способом с учётом регистра;
- измерения `account`, `category`, `merchant` объединяются через AND;
- значения внутри одного `selected`-измерения объединяются через OR;
- пустой `selected`-фильтр невалиден;
- неизвестные ID счетов, категорий и merchants возвращают `ENTITY_NOT_FOUND`;
- ответ всегда содержит нормализованные фильтры: `filters.account={scope,ids}`, `filters.category={scope,role,ids}`, `filters.merchant={scope,ids,payees}`;
- политики фильтрации имеют точные значения `account_filter=report_side`, `category_filter=exact_tag_id`, `merchant_identity=merchant_then_payee_exact`;
- неизвестные аргументы и единственные формы `account_id`, `category_id`, `merchant_id`, `payee` отклоняются.

Нормативный output contract:

- named period задаётся как `period=billing_period|week|month|year` с целым `period_offset`; custom range требует одновременно `start_date` и `end_date`;
- старые magic values внутри `start_date`, неполный custom range и смешение named/custom selectors отклоняются;
- внутренний диапазон полуоткрытый, а `start_date` и `end_date` ответа включительные; ответ также возвращает `end_exclusive`;
- Plans принимает billing period и сохраняет calendar `Budget.date` anchor даже при rollover 29–31;
- значение `value` зависит только от `report`: `income` берёт доходную сторону, `outcome` берёт расходную сторону, `net` возвращает `income - outcome`;
- `income` и `outcome` всегда неотрицательны; отрицательным может быть только `net`;
- суммы сохраняют precision API и не получают дополнительного округления;
- `currency_mode=split` возвращает `currencies`, `totals.by_currency` и отдельную запись `groups[]` для каждой пары `(key, currency)`; каждая группа содержит `currency` и `value`;
- `currency_mode=scalar` возвращает `totals` с полями `currency`, `income`, `outcome`, `value`, `transaction_count` и scalar `value` в каждой группе; в этой форме нет `currencies` и `totals.by_currency`;
- JSON-объекты ответа не используют duplicate keys;
- для `split` identity группы — пара `(key, currency)`;
- `transaction_count` считает distinct transactions; при `tag_policy=primary_tag` сумма group counts сходится с верхнеуровневым `transaction_count`, потому что дополнительные теги не размножают транзакцию по группам;
- сортировка: `currency` ascending, затем `abs(value)` descending для `net`, иначе `value` descending, затем NFC-normalized `name` ascending и NFC-normalized `key` ascending.

APK подтверждает дополнительные контракты:

- Analytics строит независимые отчёты `INCOME` и `OUTCOME`; чистый результат равен `income - outcome`.
- Для category budget метод `BUDGET` использует пользовательский план. Метод `MEAN` возвращает среднее исторических ненулевых значений только при наличии значения во всех выбранных исторических периодах; иначе возвращает `0`.
- «Запас денег» в Balance Trend равен положительному текущему балансу, делённому на средний месячный расход за три предыдущих месяца.
- Expense Trend считает `delta = last - first` и `percent = delta / abs(first) * 100`; при `first = 0` используется знаковый fallback `-100`, `0` или `100`.

## Контекст остальных разделов приложения

Top-level navigation Android 26.6 включает «Счета», «Операции», «Планы», «Аналитика» и «Ещё».

- «Счета» показывает общий баланс, банковские подключения и список счетов; из раздела доступно добавление счёта и переход к аналитической диаграмме.
- «Операции» группирует записи по датам и разделяет текущие, новые и будущие операции. Доступны поиск и фильтры по типу, периоду, счетам, основным и дополнительным категориям и местам.
- «Будущие» показывает плановые операции по датам. Для marker доступны действия: сохранить как факт, связать план с фактом, изменить, удалить один marker или удалить цепочку. Эти действия подтверждают, что план, marker и фактическая транзакция — разные состояния одного пользовательского сценария.
- Форма операции поддерживает расход, доход, перевод и долг. QR-сканирование — отдельный способ ввода.
- «Ещё» содержит категории, места, совместный доступ, SMS-журнал, уведомления, настройки аккаунта, справку и поддержку.

Не изучались глубоко внутренние экраны категорий, мест, аккаунта и уведомлений. Отдельный календарный экран, если он существует помимо «Будущих», не подтверждён. Эти области не используются как источник расчётных правил текущей постановки.

## Общие правила расчёта

1. Plans, forecast и Analytics используют один classifier счетов и переводов.
2. Plans и Analytics используют один resolver платёжного периода.
3. Собственные значения родительской категории хранятся отдельно от суммы дочерних категорий.
4. Budget, reminder и marker не должны считать одну плановую сумму дважды.
5. Состояния `processed` и `deleted` относятся к `ReminderMarker` и определяют его участие в плане и прогнозе.
6. Удаляющие tombstones и монотонный `serverTimestamp` относятся к применению `/v8/diff/`.
7. Изменение через API считается подтверждённым, только если force-fetch возвращает ожидаемые поля сущности или ожидаемое состояние удаления. Новый `serverTimestamp` без подтверждения самой сущности недостаточен.
8. `factWithRefund` использует сохранённую policy в income-vs-expense и принудительный `NONE` в balance-mode; raw `fact` остаётся отдельным полем и используется в итоговой формуле вместе с резервом.
9. `aggregate_budget` (`ALL`), `category_budget` и остаточный резерв Plans — разные поля. `ALL` не участвует в `for_balance` покатегорийного Plans.

Минимальные входы общего classifier:

| Решение | Поля API | Статус |
|---|---|---|
| Граница баланса | `Account.inBalance` обеих сторон | Подтверждено официальной help-статьёй |
| Накопления | `Account.savings`, стороны `Transaction` | Поля API подтверждены; UI-семантика наблюдалась |
| Кредит, кредитная карта, долг | тип/`creditLimit`, обе стороны `Transaction` | Debt имеет приоритет; затем `loan` или `ccard && creditLimit>0`; направление задаётся balance-boundary side |
| Категория и возврат | первый tag, `Tag.showIncome/showOutcome`, обе стороны account | Возврат требует одинаковый account и одностороннюю видимость основной категории |

## Открытые вопросы

- Чем отличаются внутренние режимы `BALANCE` и `BUDGET_LIMIT` при одинаковом наборе переключателей?
- Переносится ли перерасход или остаток бюджета в следующий период?
- Какой горизонт применяется к прогнозу и как marker связывается с фактической транзакцией?
- Какой часовой пояс и локаль задают границу платёжного периода?

Подтверждённые ответы, которые больше не являются открытыми вопросами:

- budgets детей поднимаются через unlocked-родителя; locked-родитель сохраняет собственный budget, а facts/planned/processed продолжают подниматься;
- Plans включает любой счёт с `inBalance=true`, в том числе архивный, и исключает `inBalance=false` из opening/facts кроме внешней стороны boundary transfer;
- APK конвертирует сумму в основную валюту пользователя курсом на дату операции или снимка через внутренний недокументированный метод. Обычный публичный OAuth-токен к нему доступа не имеет, поэтому skill намеренно не повторяет этот запрос и использует только текущий `Instrument.rate` из `/v8/diff/`; исторические мультивалютные итоги могут отличаться от APK.
- refund определяется основной категорией, одинаковым account на обеих сторонах и односторонней парой `showIncome/showOutcome`; `INCOME_OUTCOME_AND_REFUNDS` net-ит уже агрегированное дерево.

Каждый вопрос закрывается одним из четырёх способов: официальным источником, надёжно восстановленным выражением APK, обезличенным наблюдением существующего профиля или явно согласованным ограничением первой версии. Создание тестового профиля не требуется. Поведение, которое R8 не позволил восстановить однозначно, не считается подтверждённым только по декомпиляции.

## Не входит в первую версию

- воспроизведение интерфейса ZenMoney;
- изменение write-инструментов до фиксации read/analyze контрактов;
- публикация скриншотов или реальных финансовых данных;
- объединение валют без подтверждённой политики пересчёта;
- скрытая аппроксимация отчёта, для которого недостаточно API-данных;
- новые режимы сверх двух режимов, доступных в проверенной версии приложения.

## Приёмка постановки

- [ ] Каждый расчётный элемент связан с API-фактом, наблюдением UI или явным допущением.
- [ ] Golden dataset покрывает переводы между своими счетами, накопления в обе стороны, кредитную карту и платёж по ней, долг, возврат, parent/child categories, mixed currencies, прогноз on/off и границы периода 29–31.
- [ ] Периоды, classifier, category policy и forecast policy используются совместно Plans и Analytics.
- [ ] Открытые формулы не реализуются до подтверждения или явного ограничения.
- [ ] Mixed-currency breakdown и ошибка `MIXED_CURRENCY` покрыты contract tests.
- [ ] При смене major-версии приложения все UI-наблюдения помечаются непроверенными до повторного walkthrough.
- [ ] В Git отсутствуют токены, runtime-конфиги, кэш, реальные суммы, названия счетов и сырые скриншоты.
