ALTER TABLE knowledge_bases ADD COLUMN chunk_size integer NOT NULL DEFAULT 800 CHECK(chunk_size BETWEEN 200 AND 4000);
ALTER TABLE knowledge_bases ADD COLUMN chunk_overlap integer NOT NULL DEFAULT 120 CHECK(chunk_overlap>=0 AND chunk_overlap<=chunk_size/2);
ALTER TABLE knowledge_bases ADD COLUMN processing_revision integer NOT NULL DEFAULT 1;
ALTER TABLE documents ADD COLUMN revision integer NOT NULL DEFAULT 1;
ALTER TABLE documents ADD COLUMN source_digest text;
ALTER TABLE documents ADD COLUMN chunk_size integer NOT NULL DEFAULT 800;
ALTER TABLE documents ADD COLUMN chunk_overlap integer NOT NULL DEFAULT 120;
ALTER TABLE documents ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE documents ADD COLUMN vector_model_id text;
ALTER TABLE documents ADD COLUMN vector_model_revision integer;
ALTER TABLE documents ADD COLUMN last_parse_error text;
ALTER TABLE document_chunks ADD COLUMN document_revision integer NOT NULL DEFAULT 1;
CREATE TABLE document_revisions (
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    name text NOT NULL,
    size integer NOT NULL,
    source_digest text,
    chunk_size integer NOT NULL,
    chunk_overlap integer NOT NULL,
    parse_method text NOT NULL DEFAULT 'text' CHECK(parse_method IN ('text','ocr')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(document_id,revision)
);
INSERT INTO document_revisions(document_id,revision,name,size,chunk_size,chunk_overlap,created_at)
SELECT id,revision,name,size,chunk_size,chunk_overlap,created_at FROM documents;
CREATE INDEX document_chunks_revision ON document_chunks(document_id,document_revision,position);
CREATE INDEX documents_digest ON documents(knowledge_id,source_digest);
