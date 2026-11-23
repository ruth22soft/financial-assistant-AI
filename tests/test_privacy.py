from money_agent.privacy import mask_account_numbers


def test_masks_account_number_but_keeps_amount_and_date():
    sms = "Dear customer, your account 1000123456789 was debited with ETB 1,250.00 on 2026-11-23 at Bole Cafe."
    masked = mask_account_numbers(sms)
    assert "1000123456789" not in masked
    assert "****6789" in masked
    assert "ETB 1,250.00" in masked
    assert "2026-11-23" in masked


def test_masks_grouped_card_numbers_and_phone_numbers():
    assert mask_account_numbers("Card 4111 1111 1111 1234 used") == "Card ****1234 used"
    assert mask_account_numbers("Call 0911234567.") == "Call ****4567."


def test_leaves_short_numbers_alone():
    text = "Paid 80.00 birr, ref 12345, on 23/11/2026"
    assert mask_account_numbers(text) == text
