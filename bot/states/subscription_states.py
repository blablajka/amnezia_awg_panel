"""
FSM состояния для процесса покупки подписки.
"""
from aiogram.fsm.state import State, StatesGroup


class BuySubscription(StatesGroup):
    """Состояния покупки подписки."""
    select_plan = State()       # Выбор тарифа
    enter_promo = State()       # Ввод промокода
    enter_email = State()       # Ввод email для чека (ФЗ-54)
    confirm_payment = State()   # Подтверждение оплаты
