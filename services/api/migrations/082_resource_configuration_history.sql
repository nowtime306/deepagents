-- Extend the existing audit log; this is configuration evidence, not another version store.
ALTER TABLE audit_events ALTER COLUMN actor_id DROP NOT NULL;
ALTER TABLE audit_events ADD COLUMN resource_type text;
ALTER TABLE audit_events ADD COLUMN resource_id text;
ALTER TABLE audit_events ADD COLUMN resource_name text;
ALTER TABLE audit_events ADD COLUMN owner_id uuid;
ALTER TABLE audit_events ADD COLUMN operation text;
ALTER TABLE audit_events ADD COLUMN before_revision integer;
ALTER TABLE audit_events ADD COLUMN after_revision integer;
ALTER TABLE audit_events ADD COLUMN source text;
ALTER TABLE audit_events ADD COLUMN change_id text;
ALTER TABLE audit_events ADD COLUMN changes jsonb;
ALTER TABLE audit_events ADD COLUMN categories jsonb;
ALTER TABLE audit_events ADD COLUMN baseline boolean NOT NULL DEFAULT false;
CREATE INDEX configuration_history_resource ON audit_events(resource_type,resource_id,id DESC)
 WHERE resource_type IS NOT NULL;

-- These resources had no general configuration revision; processing revisions keep their own meaning.
ALTER TABLE mcp_servers ADD COLUMN configuration_revision integer NOT NULL DEFAULT 1;
ALTER TABLE knowledge_bases ADD COLUMN configuration_revision integer NOT NULL DEFAULT 1;
CREATE FUNCTION advance_configuration_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE field text;
BEGIN
 FOREACH field IN ARRAY string_to_array(TG_ARGV[0],',') LOOP
  IF to_jsonb(NEW)->field IS DISTINCT FROM to_jsonb(OLD)->field THEN
   NEW.configuration_revision := OLD.configuration_revision+1;
   RETURN NEW;
  END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER configuration_revision BEFORE UPDATE ON mcp_servers FOR EACH ROW
 EXECUTE FUNCTION advance_configuration_revision('name,url,token_ciphertext,enabled,allow_private,transport,oauth_required,command_json,tools');
CREATE TRIGGER configuration_revision BEFORE UPDATE ON knowledge_bases FOR EACH ROW
 EXECUTE FUNCTION advance_configuration_revision('name,embedding_model_id,rerank_model_id,chunk_size,chunk_overlap,keyword_min_coverage');

CREATE FUNCTION configuration_safe_value(field text, value jsonb, revision integer) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE origin text[];
BEGIN
 IF value IS NULL OR value='null'::jsonb THEN RETURN 'null'::jsonb; END IF;
 IF field LIKE '%ciphertext' THEN
   RETURN jsonb_build_object('configured',value #>> '{}' <> '', 'credential_revision',revision);
 END IF;
 IF field IN ('base_url','url') THEN
   origin := regexp_match(value #>> '{}','^(https?://)(?:[^/@]*@)?([A-Za-z0-9.-]+)(:[0-9]+)?');
   RETURN jsonb_build_object('origin',coalesce(origin[1]||origin[2]||coalesce(origin[3],''),'地址已隐藏'),
     'digest',encode(sha256(convert_to(value::text,'UTF8')),'hex'));
 END IF;
 -- Only explicitly typed, bounded configuration fields may be shown verbatim.
 IF field IN ('name','model','kind','protocol','provider','transport','embedding_model_id','rerank_model_id',
   'enabled','is_default','supports_images','supports_tools','max_output_tokens','temperature','allow_private',
   'oauth_required','primary_model','candidates','failures','cooldown','require_images','require_tools',
   'require_native','chunk_size','chunk_overlap','keyword_min_coverage','processing_revision') THEN
   RETURN value;
 END IF;
 RETURN jsonb_build_object('redacted',true,'digest',encode(sha256(convert_to(value::text,'UTF8')),'hex'));
END $$;

CREATE FUNCTION record_configuration_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
 before_data jsonb := CASE WHEN TG_OP='INSERT' THEN '{}'::jsonb ELSE to_jsonb(OLD) END;
 after_data jsonb := CASE WHEN TG_OP='DELETE' THEN '{}'::jsonb ELSE to_jsonb(NEW) END;
 current_data jsonb; field text; delta jsonb := '{}'; classes jsonb := '[]';
 prior_revision integer; next_revision integer; actor text; owner uuid; identifier text;
 fields text[]; item text; model_revision integer; model_label text;
BEGIN
 current_data := CASE WHEN TG_OP='DELETE' THEN before_data ELSE after_data END;
 fields := string_to_array(TG_ARGV[1],',');
 prior_revision := coalesce((before_data->>'configuration_revision')::integer,(before_data->>'revision')::integer);
 next_revision := coalesce((after_data->>'configuration_revision')::integer,(after_data->>'revision')::integer);
 IF TG_TABLE_NAME='model_capability_declarations' THEN
   SELECT revision,name INTO model_revision,model_label FROM model_configs WHERE id=current_data->>'model_id';
   next_revision := CASE WHEN TG_OP='DELETE' THEN NULL ELSE model_revision END;
   prior_revision := CASE WHEN TG_OP='INSERT' THEN NULL ELSE model_revision END;
   -- A normal model save has already recorded its body change in this same transaction/request.
   SELECT a.before_revision INTO model_revision FROM audit_events a
    WHERE a.resource_type='model' AND a.resource_id=current_data->>'model_id'
     AND a.change_id=nullif(current_setting('app.configuration_change',true),'')
     AND a.method='CONFIG_UPDATE' AND a.after_revision=next_revision
    ORDER BY a.id DESC LIMIT 1;
   IF FOUND AND TG_OP<>'INSERT' THEN prior_revision := model_revision; END IF;
 END IF;
 FOREACH field IN ARRAY fields LOOP
   IF before_data->field IS DISTINCT FROM after_data->field THEN
     delta := delta || jsonb_build_object(field,jsonb_build_object(
       'before',configuration_safe_value(field,before_data->field,prior_revision),
       'after',configuration_safe_value(field,after_data->field,next_revision)));
     IF field LIKE '%ciphertext' THEN
       delta := jsonb_set(delta,ARRAY[field,'credential_change'],to_jsonb(CASE
         WHEN coalesce(after_data->>field,'')='' THEN '已清除'
         WHEN coalesce(before_data->>field,'')='' THEN '已设置' ELSE '已更换' END));
     END IF;
     item := CASE
       WHEN field IN ('enabled','allow_private','oauth_required') THEN '启停/权限变化'
       WHEN field IN ('base_url','url','command_json','slug','ref','subdirectory','endpoint_ciphertext') THEN '审批目标变化'
       WHEN field IN ('kind','protocol','supports_images','supports_tools','transport','target_key','source_key','dimensions','evidence_digest') THEN '兼容性变化'
       WHEN field IN ('model','temperature','max_output_tokens','candidates','embedding_model_id','rerank_model_id',
                      'chunk_size','chunk_overlap','keyword_min_coverage','failures','cooldown') THEN '行为变化'
       ELSE '普通维护' END;
     IF NOT classes ? item THEN classes := classes || to_jsonb(item); END IF;
   END IF;
 END LOOP;
 IF delta='{}'::jsonb AND TG_OP='UPDATE' THEN
   IF TG_TABLE_NAME='model_configs' AND prior_revision IS DISTINCT FROM next_revision THEN
     -- Preserve the save's real OLD/NEW revision even when only a separate capability row changes.
     delta := jsonb_build_object('revision',jsonb_build_object('before',prior_revision,'after',next_revision));
     classes := '["普通维护"]'::jsonb;
   ELSE RETURN NEW;
   END IF;
 END IF;
 actor := nullif(current_setting('app.configuration_actor',true),'');
 owner := (current_data->>'owner_id')::uuid;
 identifier := coalesce(current_data->>'id',current_data->>'owner_id',current_data->>'model_id');
 INSERT INTO audit_events(actor_id,method,resource_path,status_code,resource_type,resource_id,resource_name,
 owner_id,operation,before_revision,after_revision,source,change_id,changes,categories)
 VALUES(CASE WHEN actor ~ '^[0-9a-f-]{36}$' THEN actor::uuid ELSE NULL END,'CONFIG_'||TG_OP,
 '/configuration/'||TG_ARGV[0],200,TG_ARGV[0],identifier,coalesce(current_data->>'name',model_label,TG_ARGV[0]),
 owner,TG_OP,prior_revision,next_revision,CASE WHEN actor ~ '^[0-9a-f-]{36}$' THEN 'user' ELSE coalesce(actor,'system:application') END,
 coalesce(nullif(current_setting('app.configuration_change',true),''),'transaction:'||txid_current()::text),delta,classes);
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE TRIGGER configuration_history AFTER INSERT OR UPDATE OR DELETE ON model_configs FOR EACH ROW
 EXECUTE FUNCTION record_configuration_change('model','name,model,base_url,api_key_ciphertext,max_output_tokens,temperature,enabled,is_default,kind,provider,protocol,supports_images');
CREATE TRIGGER configuration_history AFTER INSERT OR UPDATE OR DELETE ON model_capability_declarations FOR EACH ROW
 EXECUTE FUNCTION record_configuration_change('model','supports_tools');
CREATE TRIGGER configuration_history AFTER INSERT OR UPDATE OR DELETE ON mcp_servers FOR EACH ROW
 EXECUTE FUNCTION record_configuration_change('mcp','name,url,token_ciphertext,enabled,allow_private,transport,oauth_required,command_json,tools');
CREATE TRIGGER configuration_history AFTER INSERT OR UPDATE OR DELETE ON model_routes FOR EACH ROW
 EXECUTE FUNCTION record_configuration_change('route','name,primary_model,candidates,failures,cooldown,require_images,require_tools,require_native,enabled');
CREATE TRIGGER configuration_history AFTER INSERT OR UPDATE OR DELETE ON knowledge_bases FOR EACH ROW
 EXECUTE FUNCTION record_configuration_change('knowledge','name,embedding_model_id,rerank_model_id,chunk_size,chunk_overlap,keyword_min_coverage,processing_revision');
CREATE TRIGGER configuration_history AFTER INSERT OR UPDATE OR DELETE ON memory_settings FOR EACH ROW
 EXECUTE FUNCTION record_configuration_change('memory','embedding_model_id,enabled');
CREATE TRIGGER configuration_history AFTER INSERT OR UPDATE OR DELETE ON repositories FOR EACH ROW
 EXECUTE FUNCTION record_configuration_change('repository','name,slug,ref,subdirectory,token_ciphertext,enabled');
CREATE TRIGGER configuration_history AFTER INSERT OR UPDATE OR DELETE ON notification_channels FOR EACH ROW
 EXECUTE FUNCTION record_configuration_change('notification','name,kind,endpoint_ciphertext,secret_ciphertext,allow_private,enabled');

-- Existing rows have no reconstructable past. Record only this explicitly labelled rollout baseline.
DO $$
DECLARE tab text; typ text;
BEGIN
 FOREACH tab IN ARRAY ARRAY['model_configs','mcp_servers','model_routes','knowledge_bases','memory_settings','repositories','notification_channels'] LOOP
  typ := CASE tab WHEN 'model_configs' THEN 'model' WHEN 'mcp_servers' THEN 'mcp' WHEN 'model_routes' THEN 'route'
   WHEN 'knowledge_bases' THEN 'knowledge' WHEN 'memory_settings' THEN 'memory' WHEN 'repositories' THEN 'repository' ELSE 'notification' END;
  EXECUTE format('INSERT INTO audit_events(method,resource_path,status_code,resource_type,resource_id,resource_name,owner_id,operation,after_revision,source,change_id,changes,categories,baseline)
   SELECT ''CONFIG_BASELINE'',''/configuration/'||typ||''',200,%L,coalesce(d->>''id'',d->>''owner_id''),coalesce(d->>''name'',%L),
   (d->>''owner_id'')::uuid,''BASELINE'',coalesce((d->>''configuration_revision'')::integer,(d->>''revision'')::integer),
   ''system:migration082'',''migration082'',''{}''::jsonb,''[]''::jsonb,true FROM (SELECT to_jsonb(t) d FROM %I t) q',typ,typ,tab);
 END LOOP;
END $$;
-- Receiver identity is frozen independently from route/model configuration revisions.
ALTER TABLE model_route_selections ADD COLUMN receiver_digest text;
-- Old selections have no reliable target evidence and require an explicit recovery review.
ALTER TABLE notification_channels ADD COLUMN target_digest text;
-- Existing channels get an opaque stable identity. No ciphertext or its digest enters audit output.
UPDATE notification_channels SET target_digest=encode(sha256(convert_to(endpoint_ciphertext,'UTF8')),'hex');
ALTER TABLE notification_deliveries ADD COLUMN target_digest text;
ALTER TABLE notification_deliveries ADD COLUMN target_summary jsonb;
ALTER TABLE notification_deliveries ADD COLUMN message_snapshot jsonb;
ALTER TABLE notification_deliveries ADD COLUMN retry_of uuid REFERENCES notification_deliveries(id);
ALTER TABLE notification_deliveries DROP CONSTRAINT notification_deliveries_notification_id_channel_id_key;
CREATE UNIQUE INDEX notification_delivery_initial ON notification_deliveries(notification_id,channel_id) WHERE retry_of IS NULL;
ALTER TABLE notification_deliveries DROP CONSTRAINT notification_deliveries_status_check;
ALTER TABLE notification_deliveries ADD CONSTRAINT notification_deliveries_status_check
 CHECK(status IN ('PENDING','SENDING','SUCCEEDED','FAILED','UNKNOWN','CANCELLED','PAUSED'));
CREATE FUNCTION freeze_notification_delivery() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.retry_of IS NULL THEN
  SELECT c.target_digest,jsonb_build_object('kind',c.kind,'host',c.host_hint,'identity',c.target_digest)
   INTO NEW.target_digest,NEW.target_summary FROM notification_channels c WHERE c.id=NEW.channel_id;
  SELECT jsonb_build_object('id',n.id,'title',n.title,'message',n.message,'target_kind',n.target_kind,
    'target_id',n.target_id,'created_at',n.created_at) INTO NEW.message_snapshot
   FROM notifications n WHERE n.id=NEW.notification_id;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER notification_delivery_freeze BEFORE INSERT ON notification_deliveries FOR EACH ROW
 EXECUTE FUNCTION freeze_notification_delivery();
