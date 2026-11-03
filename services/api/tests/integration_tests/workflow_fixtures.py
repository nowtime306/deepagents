"""Real running workflow roots for direct ledger callback tests."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from app.workflow_definition import default_definition


async def ledger_run(database, owner):
    workflow, run = uuid4(), uuid4()
    definition = default_definition().model_dump()
    await database.execute(
        "INSERT INTO workflows(id,owner_id,name,model_id,definition) VALUES(%s,%s,%s,'test',%s)",
        (workflow, owner, "账本回调-" + str(workflow), Jsonb(definition)),
    )
    await database.execute(
        "INSERT INTO workflow_runs(id,workflow_id,status,input,snapshot) VALUES(%s,%s,'RUNNING','test',%s)",
        (run, workflow, Jsonb({"definition": definition})),
    )
    return run
