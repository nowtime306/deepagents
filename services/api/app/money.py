"""Immutable price snapshots and transactional per-currency monetary reservations."""

from decimal import ROUND_UP, Decimal

from app.errors import fail

MILLION = Decimal(1000000)
QUANTUM = Decimal("0.000000001")


def cost(price, usage):
    incoming, outgoing = usage["input_tokens"], usage["output_tokens"]
    value = incoming * price["input_per_million"] + outgoing * price["output_per_million"]
    if price["scheme"] == "split":
        for key, rate in (
            ("cache_read_tokens", "cache_read_per_million"),
            ("cache_creation_tokens", "cache_write_per_million"),
        ):
            detail = usage.get(key)
            difference = price[rate] - price["input_per_million"]
            if detail is None and difference:
                return None
            value += (detail or 0) * difference
        if (usage.get("cache_read_tokens") or 0) + (usage.get("cache_creation_tokens") or 0) > incoming:
            return None
    return (value / MILLION).quantize(QUANTUM, rounding=ROUND_UP)


async def reserve(connection, owner, call, model, incoming, outgoing):
    from app.extra_cost_budget import reserve as reserve_extra

    await _reserve_tokens(connection, owner, call, model, incoming, outgoing)
    await reserve_extra(connection, owner, call, model, "call", Decimal(1))


async def _reserve_tokens(connection, owner, call, model, incoming, outgoing):
    # Lock both possible currencies in a stable order, including a budget that is being edited.
    await connection.execute("SELECT pg_advisory_xact_lock(710032,hashtext(%s))", (str(owner),))
    price = await (
        await connection.execute(
            "SELECT * FROM model_price_versions WHERE model_id=%s "
            "AND effective_at<=now() ORDER BY effective_at DESC,created_at DESC,id DESC LIMIT 1",
            (model,),
        )
    ).fetchone()
    budgets = await (
        await connection.execute("SELECT * FROM money_budgets WHERE owner_id=%s AND enabled", (owner,))
    ).fetchall()
    if not price:
        if budgets:
            fail(422, "MONEY_PRICE_REQUIRED", "已启用金额预算，当前模型缺少生效的价格版本，未发送请求")
        return
    await connection.execute(
        "UPDATE model_usage SET price_version_id=%s,input_rate=%s,output_rate=%s,currency=%s,"
        "cost_status='PENDING' WHERE call_id=%s",
        (price["id"], price["input_per_million"], price["output_per_million"], price["currency"], call),
    )
    budget = next((b for b in budgets if b["currency"] == price["currency"]), None)
    if not budget:
        return
    window = await (
        await connection.execute(
            "SELECT anchor_at+floor(extract(epoch FROM(now()-anchor_at))/"
            "(period_days*86400))*make_interval(days=>period_days) AS start,now() AS current "
            "FROM money_budgets WHERE owner_id=%s AND currency=%s",
            (owner, price["currency"]),
        )
    ).fetchone()
    # Old unknown requests stay frozen across renewal and cannot be evaded by shortening a period.
    from app.extra_cost_budget import spent as combined_spent

    spent = {"value": await combined_spent(connection, owner, price["currency"], window["start"])}
    rate = max(price["input_per_million"], price["cache_read_per_million"] or 0, price["cache_write_per_million"] or 0)
    amount = ((incoming * rate + outgoing * price["output_per_million"]) / MILLION).quantize(QUANTUM, rounding=ROUND_UP)
    if spent["value"] + amount > budget["amount"]:
        fail(422, "MONEY_BUDGET_EXCEEDED", "金额预算不足，未发送新模型请求；未知调用仍冻结预留金额")
    await connection.execute(
        "INSERT INTO model_money_holds(call_id,owner_id,currency,window_start,window_end,reserved) "
        "VALUES(%s,%s,%s,%s,%s+make_interval(days=>%s),%s)",
        (call, owner, price["currency"], window["start"], window["start"], budget["period_days"], amount),
    )


async def settle(connection, call, usage):
    from app.extra_cost_budget import settle as settle_extra

    await _settle_tokens(connection, call, usage)
    if usage is not None:
        await settle_extra(connection, call, "call", Decimal(1))


async def _settle_tokens(connection, call, usage):
    row = await (
        await connection.execute("SELECT owner_id,price_version_id,status FROM model_usage WHERE call_id=%s", (call,))
    ).fetchone()
    if not row or not row["price_version_id"]:
        return
    await connection.execute("SELECT pg_advisory_xact_lock(710032,hashtext(%s))", (str(row["owner_id"]),))
    price = await (
        await connection.execute("SELECT * FROM model_price_versions WHERE id=%s", (row["price_version_id"],))
    ).fetchone()
    amount = cost(price, usage) if usage else None
    await connection.execute(
        "UPDATE model_usage SET estimated_cost=%s,cost_status=%s WHERE call_id=%s",
        (amount, "ESTIMATED" if amount is not None else "UNKNOWN", call),
    )
    if amount is not None:
        await connection.execute(
            "UPDATE model_money_holds SET actual=%s WHERE call_id=%s AND confirmed_at IS NULL", (amount, call)
        )
