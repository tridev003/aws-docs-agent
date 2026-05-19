# CloudFront sits in front of the ALB to get HTTPS without owning a domain.
# AWS auto-issues a *.cloudfront.net cert, the ALB stays on HTTP.
#
# Two non-obvious bits for Streamlit:
#   - WebSocket through CloudFront works only with CachingDisabled +
#     AllViewer origin request policy, which forward all headers/cookies.
#   - http -> https redirect at the edge keeps users from accidentally
#     opening the ALB on port 80.

# Managed policies (AWS publishes these with stable IDs)
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}

resource "aws_cloudfront_distribution" "this" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "${var.name_prefix} HTTPS front for Streamlit ALB"
  price_class     = "PriceClass_100" # NA + EU edges only, cheaper

  origin {
    domain_name = var.alb_dns_name
    origin_id   = "alb"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # ALB listener is HTTP
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"

    allowed_methods = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods  = ["GET", "HEAD"]

    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id

    compress = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
