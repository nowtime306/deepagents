ALTER TABLE agents ADD COLUMN knowledge_ids jsonb NOT NULL DEFAULT '[]';
ALTER TABLE agents ADD COLUMN skill_ids jsonb NOT NULL DEFAULT '[]';
ALTER TABLE agents ADD COLUMN mcp_ids jsonb NOT NULL DEFAULT '[]';
UPDATE agents SET knowledge_ids=b.knowledge_ids,skill_ids=b.skill_ids,mcp_ids=b.mcp_ids
FROM capability_bindings b WHERE b.owner_id=agents.owner_id;
