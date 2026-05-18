output "bucket_name" {
  value       = aws_s3_bucket.index.bucket
  description = "Name of the bucket holding the FAISS index."
}

output "bucket_arn" {
  value       = aws_s3_bucket.index.arn
  description = "ARN of the index bucket."
}
