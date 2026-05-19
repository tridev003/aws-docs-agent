variable "name_prefix" {
  type = string
}

variable "alb_dns_name" {
  type        = string
  description = "DNS name of the upstream ALB."
}
