from app.demo_data import DEMO


def test_test_task_receipt_quantities():
    assert DEMO == {
        "SKU-101": ("Органайзер", 10, 6),
        "SKU-102": ("Контейнер", 5, 0),
        "SKU-103": ("Корзина", 0, 4),
    }
    assert sum(n + s for _, n, s in DEMO.values()) == 25
