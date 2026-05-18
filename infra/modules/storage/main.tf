# S3 bucket storing the FAISS index artifacts (faiss.index, metadata.jsonl,
# manifest.json). The application reads from this bucket at boot via
# INDEX_S3_BUCKET / INDEX_S3_PREFIX env vars.

resource "random_id" "suffix" {
  byte_length = 3
}

resource "aws_s3_bucket" "index" {
  # Bucket names are globally unique, append a short random suffix to avoid
  # collisions on re-create.
  bucket = "${var.name_prefix}-index-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_public_access_block" "index" {
  bucket                  = aws_s3_bucket.index.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "index" {
  bucket = aws_s3_bucket.index.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "index" {
  bucket = aws_s3_bucket.index.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "index" {
  bucket = aws_s3_bucket.index.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "index" {
  bucket = aws_s3_bucket.index.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
