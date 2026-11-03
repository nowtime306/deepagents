-- Model settings may be removed; actual selection history remains an owned audit record.
ALTER TABLE model_route_selections DROP CONSTRAINT model_route_selections_route_id_fkey;
ALTER TABLE model_route_selections ALTER COLUMN route_id DROP NOT NULL;
ALTER TABLE model_route_selections ADD FOREIGN KEY(route_id) REFERENCES model_routes(id) ON DELETE SET NULL;
ALTER TABLE model_route_selections DROP CONSTRAINT model_route_selections_model_id_fkey;
