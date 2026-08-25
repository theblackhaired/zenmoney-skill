import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


PLAN_SETTING_FLAGS = {
    "EXCLUDE_TRANSFER_FROM_SAVINGS",
    "EXCLUDE_TRANSFER_TO_SAVINGS",
    "EXCLUDE_TRANSFER_FROM_LOANS",
    "EXCLUDE_TRANSFER_TO_LOANS",
    "EXCLUDE_TRANSFER_FROM_DEBTS",
    "EXCLUDE_TRANSFER_TO_DEBTS",
    "EXCLUDE_TRANSFER_FROM_OTHER_ACCOUNTS",
    "EXCLUDE_TRANSFER_TO_OTHER_ACCOUNTS",
}


def _classifier():
    from zenmoney import transfer_classifier

    return transfer_classifier


def _side(
    account_id: str,
    *,
    amount: int,
    currency: str = "RUB",
    in_balance: bool,
    account_type: str = "checking",
    account_subtype: str = "checking",
    savings: bool = False,
    credit_limit: int = 0,
    known_account: bool = True,
) -> dict:
    return {
        "account_id": account_id,
        "amount": amount,
        "currency": currency,
        "in_balance": in_balance,
        "account_type": account_type,
        "account_subtype": account_subtype,
        "savings": savings,
        "credit_limit": credit_limit,
        "known_account": known_account,
    }


def _transfer(outcome_side: dict, income_side: dict) -> dict:
    return {
        "id": f"{outcome_side['account_id']}-to-{income_side['account_id']}",
        "date": "2026-07-10",
        "outcome_side": outcome_side,
        "income_side": income_side,
    }


class TransferClassifierEventContractTests(unittest.TestCase):
    def test_classify_transfer_event_preserves_both_sides_for_balance_to_balance(self):
        classifier = _classifier()
        item = _transfer(
            _side("bal-rub", amount=100, currency="RUB", in_balance=True),
            _side("bal-kzt", amount=500, currency="KZT", in_balance=True),
        )

        event = classifier.classify_transfer_event(item)

        self.assertEqual(event["kind"], "transfer")
        self.assertEqual(event["direction"], "balance_to_balance")
        self.assertEqual(event["outcome_side"], item["outcome_side"])
        self.assertEqual(event["income_side"], item["income_side"])
        self.assertEqual(event["outcome_side"]["currency"], "RUB")
        self.assertEqual(event["income_side"]["currency"], "KZT")

    def test_classify_transfer_event_preserves_off_balance_to_off_balance_without_collapsing(self):
        classifier = _classifier()
        item = _transfer(
            _side("off-rub", amount=110, currency="RUB", in_balance=False),
            _side("off-kzt", amount=600, currency="KZT", in_balance=False),
        )

        event = classifier.classify_transfer_event(item)

        self.assertEqual(event["direction"], "off_balance_to_off_balance")
        self.assertEqual(event["outcome_side"]["account_id"], "off-rub")
        self.assertEqual(event["income_side"]["account_id"], "off-kzt")
        self.assertEqual(event["outcome_side"]["amount"], 110)
        self.assertEqual(event["income_side"]["amount"], 600)

    def test_classify_transfer_event_labels_savings_credit_debt_and_other_axes(self):
        classifier = _classifier()
        rows = [
            (
                "from_savings",
                _side("savings", amount=120, in_balance=False, account_subtype="savings", savings=True),
                _side("balance", amount=120, in_balance=True),
                "savings",
                "other_accounts",
            ),
            (
                "to_savings",
                _side("balance", amount=130, in_balance=True),
                _side("savings", amount=130, in_balance=False, account_subtype="savings", savings=True),
                "other_accounts",
                "savings",
            ),
            (
                "from_credit",
                _side("credit", amount=140, in_balance=False, account_type="ccard", account_subtype="credit"),
                _side("balance", amount=140, in_balance=True),
                "loans",
                "other_accounts",
            ),
            (
                "to_credit",
                _side("balance", amount=150, in_balance=True),
                _side("credit", amount=150, in_balance=False, account_type="ccard", account_subtype="credit"),
                "other_accounts",
                "loans",
            ),
            (
                "from_debt",
                _side("debt", amount=160, in_balance=False, account_type="debt", account_subtype="debt"),
                _side("balance", amount=160, in_balance=True),
                "debts",
                "other_accounts",
            ),
            (
                "to_debt",
                _side("balance", amount=170, in_balance=True),
                _side("debt", amount=170, in_balance=False, account_type="debt", account_subtype="debt"),
                "other_accounts",
                "debts",
            ),
        ]

        for label, outcome, income, expected_outcome_axis, expected_income_axis in rows:
            with self.subTest(label=label):
                event = classifier.classify_transfer_event(_transfer(outcome, income))

                self.assertEqual(event["outcome_axis"], expected_outcome_axis)
                self.assertEqual(event["income_axis"], expected_income_axis)

    def test_account_axis_uses_apk_credit_before_savings_precedence_and_deposit_mapping(self):
        classifier = _classifier()
        credit_and_savings = classifier.classify_transfer_event(
            _transfer(
                _side(
                    "credit",
                    amount=100,
                    in_balance=False,
                    account_type="ccard",
                    savings=True,
                    credit_limit=1000,
                ),
                _side("balance", amount=100, in_balance=True),
            )
        )
        deposit = classifier.classify_transfer_event(
            _transfer(
                _side("deposit", amount=100, in_balance=False, account_type="deposit"),
                _side("balance", amount=100, in_balance=True),
            )
        )

        self.assertEqual(credit_and_savings["outcome_axis"], "loans")
        self.assertEqual(deposit["outcome_axis"], "savings")


class PlanTransferPolicyEvaluatorContractTests(unittest.TestCase):
    def test_sync_aliases_are_normalized_and_multiple_users_require_explicit_selection(self):
        classifier = _classifier()

        self.assertEqual(
            classifier.parse_plan_settings(
                '["includeOpeningBalance","excludeTransferFromLoans"]'
            ),
            {"INCLUDE_OPENING_BALANCE", "EXCLUDE_TRANSFER_FROM_LOANS"},
        )
        with self.assertRaisesRegex(ValueError, "multiple users"):
            classifier.select_plan_user([{"id": 1}, {"id": 2}])
        with self.assertRaisesRegex(ValueError, "preferences are unavailable"):
            classifier.select_plan_user([])
        self.assertEqual(
            classifier.select_plan_user([{"id": 1}, {"id": 2}], preferred_user_id=2),
            {"id": 2},
        )

    def test_budget_limit_and_unknown_endpoint_fail_explicitly(self):
        classifier = _classifier()
        event = classifier.classify_transfer_event(
            _transfer(
                _side("unknown", amount=100, in_balance=False, known_account=False),
                _side("balance", amount=100, in_balance=True),
            )
        )

        with self.assertRaisesRegex(ValueError, "BUDGET_LIMIT"):
            classifier.evaluate_plan_transfer(
                event,
                plan_balance_mode="BUDGET_LIMIT",
                plan_settings=set(),
            )
        with self.assertRaisesRegex(ValueError, "unknown account endpoint"):
            classifier.evaluate_plan_transfer(
                event,
                plan_balance_mode="EXCLUDE_OPENING_BALANCE",
                plan_settings=set(),
            )

    def test_balance_to_balance_is_neutral_even_when_no_plan_settings_are_excluded(self):
        classifier = _classifier()
        event = classifier.classify_transfer_event(
            _transfer(
                _side("bal-rub", amount=100, currency="RUB", in_balance=True),
                _side("bal-kzt", amount=500, currency="KZT", in_balance=True),
            )
        )

        evaluated = classifier.evaluate_plan_transfer(
            event,
            plan_balance_mode="BALANCE",
            plan_settings=set(),
        )

        self.assertEqual(evaluated["effects"], [])
        self.assertEqual(evaluated["net"], 0)
        self.assertEqual(evaluated["reason"], "balance_to_balance_neutral")

    def test_off_balance_to_off_balance_is_preserved_but_has_no_plan_effect_by_default(self):
        classifier = _classifier()
        event = classifier.classify_transfer_event(
            _transfer(
                _side("off-rub", amount=110, currency="RUB", in_balance=False),
                _side("off-kzt", amount=600, currency="KZT", in_balance=False),
            )
        )

        evaluated = classifier.evaluate_plan_transfer(
            event,
            plan_balance_mode="BALANCE",
            plan_settings=set(),
        )

        self.assertEqual(evaluated["effects"], [])
        self.assertEqual(evaluated["net"], 0)
        self.assertEqual(evaluated["event"]["outcome_side"]["currency"], "RUB")
        self.assertEqual(evaluated["event"]["income_side"]["currency"], "KZT")

    def test_each_directed_plan_setting_flag_excludes_only_its_matching_effect(self):
        classifier = _classifier()
        rows = [
            (
                "EXCLUDE_TRANSFER_FROM_SAVINGS",
                _side("savings", amount=120, in_balance=False, account_subtype="savings", savings=True),
                _side("balance", amount=120, in_balance=True),
                {"kind": "income", "amount": 120, "currency": "RUB", "setting": "EXCLUDE_TRANSFER_FROM_SAVINGS"},
            ),
            (
                "EXCLUDE_TRANSFER_TO_SAVINGS",
                _side("balance", amount=130, in_balance=True),
                _side("savings", amount=130, in_balance=False, account_subtype="savings", savings=True),
                {"kind": "expense", "amount": 130, "currency": "RUB", "setting": "EXCLUDE_TRANSFER_TO_SAVINGS"},
            ),
            (
                "EXCLUDE_TRANSFER_FROM_LOANS",
                _side("credit", amount=140, in_balance=False, account_type="ccard", account_subtype="credit"),
                _side("balance", amount=140, in_balance=True),
                {"kind": "income", "amount": 140, "currency": "RUB", "setting": "EXCLUDE_TRANSFER_FROM_LOANS"},
            ),
            (
                "EXCLUDE_TRANSFER_TO_LOANS",
                _side("balance", amount=150, in_balance=True),
                _side("credit", amount=150, in_balance=False, account_type="ccard", account_subtype="credit"),
                {"kind": "expense", "amount": 150, "currency": "RUB", "setting": "EXCLUDE_TRANSFER_TO_LOANS"},
            ),
            (
                "EXCLUDE_TRANSFER_FROM_DEBTS",
                _side("debt", amount=160, in_balance=False, account_type="debt", account_subtype="debt"),
                _side("balance", amount=160, in_balance=True),
                {"kind": "income", "amount": 160, "currency": "RUB", "setting": "EXCLUDE_TRANSFER_FROM_DEBTS"},
            ),
            (
                "EXCLUDE_TRANSFER_TO_DEBTS",
                _side("balance", amount=170, in_balance=True),
                _side("debt", amount=170, in_balance=False, account_type="debt", account_subtype="debt"),
                {"kind": "expense", "amount": 170, "currency": "RUB", "setting": "EXCLUDE_TRANSFER_TO_DEBTS"},
            ),
            (
                "EXCLUDE_TRANSFER_FROM_OTHER_ACCOUNTS",
                _side("off", amount=180, in_balance=False),
                _side("balance", amount=180, in_balance=True),
                {
                    "kind": "income",
                    "amount": 180,
                    "currency": "RUB",
                    "setting": "EXCLUDE_TRANSFER_FROM_OTHER_ACCOUNTS",
                },
            ),
            (
                "EXCLUDE_TRANSFER_TO_OTHER_ACCOUNTS",
                _side("balance", amount=190, in_balance=True),
                _side("off", amount=190, in_balance=False),
                {
                    "kind": "expense",
                    "amount": 190,
                    "currency": "RUB",
                    "setting": "EXCLUDE_TRANSFER_TO_OTHER_ACCOUNTS",
                },
            ),
        ]

        for setting, outcome, income, expected_effect in rows:
            with self.subTest(setting=setting):
                event = classifier.classify_transfer_event(_transfer(outcome, income))
                included = classifier.evaluate_plan_transfer(
                    event,
                    plan_balance_mode="EXCLUDE_OPENING_BALANCE",
                    plan_settings=PLAN_SETTING_FLAGS - {setting},
                )
                excluded = classifier.evaluate_plan_transfer(
                    event,
                    plan_balance_mode="EXCLUDE_OPENING_BALANCE",
                    plan_settings={setting},
                )

                self.assertEqual(included["effects"], [expected_effect])
                self.assertEqual(excluded["effects"], [])

    def test_balance_mode_ignores_transfer_exclusions_and_includes_balance_change(self):
        classifier = _classifier()
        event = classifier.classify_transfer_event(
            _transfer(
                _side("savings", amount=120, in_balance=False, account_subtype="savings", savings=True),
                _side("balance", amount=120, in_balance=True),
            )
        )

        evaluated = classifier.evaluate_plan_transfer(
            event,
            plan_balance_mode="BALANCE",
            plan_settings={"EXCLUDE_TRANSFER_FROM_SAVINGS"},
        )

        self.assertEqual(
            evaluated["effects"],
            [
                {
                    "kind": "income",
                    "amount": 120,
                    "currency": "RUB",
                    "setting": "EXCLUDE_TRANSFER_FROM_SAVINGS",
                }
            ],
        )

    def test_cross_currency_policy_uses_the_effect_side_amount_and_preserves_the_other_side(self):
        classifier = _classifier()
        event = classifier.classify_transfer_event(
            _transfer(
                _side("savings-kzt", amount=900, currency="KZT", in_balance=False, account_subtype="savings", savings=True),
                _side("bal-rub", amount=150, currency="RUB", in_balance=True),
            )
        )

        evaluated = classifier.evaluate_plan_transfer(
            event,
            plan_balance_mode="BALANCE",
            plan_settings=set(),
        )

        self.assertEqual(
            evaluated["effects"],
            [
                {
                    "kind": "income",
                    "amount": 150,
                    "currency": "RUB",
                    "setting": "EXCLUDE_TRANSFER_FROM_SAVINGS",
                }
            ],
        )
        self.assertEqual(evaluated["event"]["outcome_side"]["amount"], 900)
        self.assertEqual(evaluated["event"]["outcome_side"]["currency"], "KZT")

    def test_opening_balance_policy_is_selected_by_plan_balance_mode(self):
        classifier = _classifier()
        event = classifier.classify_opening_balance_event(
            {
                "account_id": "bal-rub",
                "amount": 1000,
                "currency": "RUB",
                "in_balance": True,
            }
        )

        balance_mode = classifier.evaluate_plan_transfer(
            event,
            plan_balance_mode="BALANCE",
            plan_settings=set(),
        )
        exclude_opening_balance_mode = classifier.evaluate_plan_transfer(
            event,
            plan_balance_mode="EXCLUDE_OPENING_BALANCE",
            plan_settings=set(),
        )
        include_opening_balance_setting = classifier.evaluate_plan_transfer(
            event,
            plan_balance_mode="EXCLUDE_OPENING_BALANCE",
            plan_settings={"INCLUDE_OPENING_BALANCE"},
        )

        self.assertEqual(balance_mode["effects"], [{"kind": "income", "amount": 1000, "currency": "RUB"}])
        self.assertEqual(exclude_opening_balance_mode["effects"], [])
        self.assertEqual(
            include_opening_balance_setting["effects"],
            [{"kind": "income", "amount": 1000, "currency": "RUB"}],
        )


if __name__ == "__main__":
    unittest.main()
