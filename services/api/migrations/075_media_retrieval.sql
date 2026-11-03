CREATE TABLE knowledge_video_jobs (
 id uuid PRIMARY KEY, knowledge_id uuid REFERENCES knowledge_bases(id) ON DELETE CASCADE,
 job_kind text NOT NULL DEFAULT 'video_index' CHECK(job_kind IN ('video_index','probe')),
 owner_id uuid NOT NULL REFERENCES users(id), model_id text NOT NULL, model_revision integer NOT NULL,
 name text NOT NULL, size bigint NOT NULL, interval_seconds integer NOT NULL CHECK(interval_seconds BETWEEN 1 AND 30),
 max_frames integer NOT NULL CHECK(max_frames BETWEEN 1 AND 12), status text NOT NULL DEFAULT 'PENDING'
 CHECK(status IN ('PENDING','RUNNING','UNKNOWN','COMPLETED','FAILED','CANCELLED')),
 cancel_requested boolean NOT NULL DEFAULT false, holds_memory boolean NOT NULL DEFAULT false,
 container_id text, image_id text, create_attempted boolean NOT NULL DEFAULT false,
 error_code text, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge_video_frames (
 image_id uuid PRIMARY KEY REFERENCES knowledge_images(id) ON DELETE CASCADE,
 job_id uuid NOT NULL REFERENCES knowledge_video_jobs(id) ON DELETE CASCADE,
 sample_seconds numeric(12,3) NOT NULL CHECK(sample_seconds>=0)
);
ALTER TABLE resource_operations DROP CONSTRAINT resource_operations_resource_kind_check;
ALTER TABLE resource_operations ADD CHECK(resource_kind IN ('memory','knowledge','media'));
