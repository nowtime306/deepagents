"""Non-token reserves share the existing per-owner currency budget lock."""

from decimal import ROUND_UP, Decimal
from uuid import uuid4

from app.errors import fail


async def spent(connection, owner, currency, start):
    unknown = await (await connection.execute(
        "SELECT 1 FROM extra_cost_usage u JOIN extra_cost_prices p ON p.id=u.price_id "
        "LEFT JOIN extra_cost_holds h ON h.call_id=u.call_id AND h.item=u.item "
        "WHERE u.owner_id=%s AND p.currency=%s AND u.amount IS NULL AND h.call_id IS NULL LIMIT 1",
        (owner, currency))).fetchone()
    if unknown:
        fail(422, "MONEY_EXTRA_UNKNOWN", "存在尚未核对的额外费用且无预留，不能按0继续消费")
    return (await (await connection.execute(
        "SELECT coalesce(sum(value),0) AS value FROM ("
        "SELECT coalesce(actual,reserved) AS value FROM model_money_holds "
        "WHERE owner_id=%s AND currency=%s AND (window_end>%s OR actual IS NULL) UNION ALL "
        "SELECT coalesce(actual,reserved) FROM extra_cost_holds "
        "WHERE owner_id=%s AND currency=%s AND (window_end>%s OR actual IS NULL) UNION ALL "
        "SELECT u.amount FROM extra_cost_usage u JOIN extra_cost_prices p ON p.id=u.price_id "
        "LEFT JOIN extra_cost_holds h ON h.call_id=u.call_id AND h.item=u.item "
        "WHERE u.owner_id=%s AND p.currency=%s AND h.call_id IS NULL AND u.created_at>=%s) s",
        (owner, currency, start, owner, currency, start, owner, currency, start))).fetchone())["value"]


async def reserve(connection, owner, call, model, unit, quantity, *, require_price=False):
    await connection.execute("SELECT pg_advisory_xact_lock(710032,hashtext(%s))", (str(owner),))
    prices = await (await connection.execute(
        "SELECT * FROM (SELECT DISTINCT ON(item) * FROM extra_cost_prices WHERE model_id=%s AND effective_at<=now() "
        "ORDER BY item,effective_at DESC,created_at DESC,id DESC) p WHERE unit=%s", (model, unit))).fetchall()
    budgets = await (await connection.execute(
        "SELECT * FROM money_budgets WHERE owner_id=%s AND enabled", (owner,))).fetchall()
    if not prices and require_price and budgets:
        fail(422, "EXTRA_PRICE_REQUIRED", "已启用金额预算，此媒体服务缺少额外计费版本，未发送请求")
    for price in prices:
        amount = (quantity * price["unit_price"]).quantize(Decimal(".000000001"), rounding=ROUND_UP)
        budget = next((b for b in budgets if b["currency"] == price["currency"]), None)
        window = await (await connection.execute(
            "SELECT now() AS current,anchor_at+floor(extract(epoch FROM(now()-anchor_at))/"
            "(period_days*86400))*make_interval(days=>period_days) AS start "
            "FROM money_budgets WHERE owner_id=%s AND currency=%s",
            (owner, price["currency"]))).fetchone()
        if budget and await spent(connection, owner, price["currency"], window["start"]) + amount > budget["amount"]:
            fail(422, "MONEY_BUDGET_EXCEEDED", "Token与额外费用合计预留超过金额预算，未发送请求")
        await connection.execute(
            "INSERT INTO extra_cost_usage(id,call_id,owner_id,item,price_id,source) "
            "VALUES(%s,%s,%s,%s,%s,'provider')", (uuid4(), call, owner, price["item"], price["id"]))
        # No budget still keeps the immutable quantity reservation for later reconciliation.
        await connection.execute(
            "INSERT INTO extra_cost_holds(call_id,item,owner_id,price_id,currency,reserved,window_start,window_end) "
            "VALUES(%s,%s,%s,%s,%s,%s,coalesce(%s,now()),coalesce(%s,now())+make_interval(days=>%s))",
            (call, price["item"], owner, price["id"], price["currency"], amount,
             window["start"] if window else None, window["start"] if window else None,
             budget["period_days"] if budget else 1))


async def settle(connection, call, unit, quantity):
    owner = await (await connection.execute(
        "SELECT owner_id FROM model_usage WHERE call_id=%s", (call,))).fetchone()
    if not owner:
        return
    # Read mutable settlement state only after the shared owner lock, including after waiting.
    await connection.execute("SELECT pg_advisory_xact_lock(710032,hashtext(%s))", (str(owner["owner_id"]),))
    rows = await (await connection.execute(
        "SELECT h.*,p.unit_price,u.quantity FROM extra_cost_holds h JOIN extra_cost_prices p ON p.id=h.price_id "
        "JOIN extra_cost_usage u ON u.call_id=h.call_id AND u.item=h.item "
        "WHERE h.call_id=%s AND p.unit=%s", (call, unit))).fetchall()
    for row in rows:
        amount = (quantity * row["unit_price"]).quantize(Decimal(".000000001"), rounding=ROUND_UP)
        if row["actual"] is not None:
            if row["actual"] != amount or row["quantity"] != quantity:
                fail(409, "EXTRA_CHARGE_CONFLICT", "额外费用已经结算，不能覆盖")
            continue
        await connection.execute("UPDATE extra_cost_holds SET actual=%s WHERE call_id=%s AND item=%s",
                                 (amount, call, row["item"]))
        await connection.execute(
            "UPDATE extra_cost_usage SET quantity=%s,amount=%s WHERE call_id=%s AND item=%s",
            (quantity, amount, call, row["item"]))
