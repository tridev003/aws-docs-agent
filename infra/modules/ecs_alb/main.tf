# ECS Fargate + Application Load Balancer.
#
# We use the account's default VPC and its public subnets so the module is
# self-contained: no NAT gateway, no VPC peering, no extra cost. The Streamlit
# container needs WebSocket support, which is why this lives behind an ALB
# (App Runner's envoy proxy drops Upgrade: websocket).

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# ----- security groups -------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${var.name_prefix}-alb"
  description = "ALB public ingress"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP from anywhere"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "task" {
  name        = "${var.name_prefix}-task"
  description = "ECS task: accepts traffic from the ALB only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "Streamlit from ALB"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ----- load balancer ---------------------------------------------------------

resource "aws_lb" "this" {
  name               = "${var.name_prefix}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.default.ids
  idle_timeout       = 120 # WebSocket connections idle between user messages.
}

resource "aws_lb_target_group" "this" {
  name        = "${var.name_prefix}-tg"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip" # required for Fargate awsvpc networking

  health_check {
    path                = "/_stcore/health"
    healthy_threshold   = 2
    unhealthy_threshold = 5
    interval            = 30
    timeout             = 10
    matcher             = "200"
  }

  # Streamlit sets a session cookie keyed to the WebSocket connection; sticky
  # sessions keep a browser glued to one task instance so reconnects don't
  # land on a different replica with a different session_state.
  stickiness {
    type            = "lb_cookie"
    cookie_duration = 3600
    enabled         = true
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }
}

# ----- ECS -------------------------------------------------------------------

resource "aws_ecs_cluster" "this" {
  name = "${var.name_prefix}-cluster"
}

resource "aws_cloudwatch_log_group" "task" {
  name              = "/ecs/${var.name_prefix}"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "this" {
  family                   = "${var.name_prefix}-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.cpu)
  memory                   = tostring(var.memory)
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${var.image_repository_url}:${var.image_tag}"
      essential = true
      portMappings = [
        { containerPort = var.container_port, protocol = "tcp" }
      ]
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "BEDROCK_CHAT_MODEL_ID", value = var.bedrock_chat_model_id },
        { name = "BEDROCK_EMBED_MODEL_ID", value = var.bedrock_embed_model_id },
        { name = "INDEX_S3_BUCKET", value = var.index_s3_bucket },
        { name = "INDEX_S3_PREFIX", value = var.index_s3_prefix },
        { name = "INDEX_LOCAL_PATH", value = "/app/data/index" },
        { name = "AGENT_MAX_TOOL_CALLS", value = tostring(var.agent_max_tool_calls) },
        { name = "AGENT_TOP_K", value = tostring(var.agent_top_k) },
        { name = "PYTHONUNBUFFERED", value = "1" },
        # Streamlit behind a proxy: trust ALB-rewritten Host/Origin.
        { name = "STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION", value = "false" },
        { name = "STREAMLIT_SERVER_ENABLE_CORS", value = "false" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.task.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "app"
        }
      }
      healthCheck = {
        command     = ["CMD-SHELL", "curl -fsS http://localhost:${var.container_port}/_stcore/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])
}

resource "aws_ecs_service" "this" {
  name            = "${var.name_prefix}-svc"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this.arn
  launch_type     = "FARGATE"
  desired_count   = var.desired_count

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true # default-VPC subnets have an IGW; no NAT needed
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.this.arn
    container_name   = "app"
    container_port   = var.container_port
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 200

  # Wait for the listener before creating tasks, otherwise ECS races and
  # marks them unhealthy before the LB starts routing.
  depends_on = [aws_lb_listener.http]
}
