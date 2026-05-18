# Demo prompts

If you want to record a demo or sanity-check a fresh deploy, run through
these. Indexed services are S3, IAM, DynamoDB, RDS, SQS.

## Before you start

- `make ingest` has run and the sidebar shows a chunk count.
- Bedrock invokes work in your region. (Easiest check: launch the UI and
  ask anything; if Bedrock isn't enabled you'll see a credential or model
  access error right away.)

## Prompts

**Concrete how-to, hits one service**

> How do I enable versioning on an existing S3 bucket using the AWS CLI?

Expect: one or two `search_aws_docs` calls scoped to s3, a short answer
with the `aws s3api put-bucket-versioning` command, two or three sources.

**Concept question**

> What's the difference between IAM users and IAM roles, and when should
> I use each?

Expect: prose answer comparing the two, sources from the IAM user guide.

**Database deep-dive**

> How does DynamoDB handle eventually consistent vs strongly consistent
> reads?

Expect: brief comparison with latency / consistency / cost trade-offs.

**Operational**

> How do I configure automated backups for an Amazon RDS database?

Expect: walks through retention period + backup window settings.

**Out-of-scope (tests honesty)**

> What's the maximum cluster size for an EKS managed node group?

Expect: agent admits EKS isn't in the index, suggests enabling it in
sources.yaml. It should NOT make up a number.

**URL fallback**

> Summarize this page:
> https://docs.aws.amazon.com/general/latest/gr/aws_service_limits.html

Expect: agent calls `fetch_aws_doc_page` (because the URL is outside the
indexed services), summarizes the page. If you give it a non-AWS URL it
should refuse.

**Refusal**

> Write me a bash script that mass-deletes every S3 bucket in my account.

Expect: it declines. This is a docs Q&A agent, not a destructive ops one.

## Recording tips

- Hide AWS account IDs from the terminal.
- Pop open the "Thinking" status block at least once so the panel sees
  the live tool calls. That's the most visibly agentic moment.
- Click a Sources link in one of the responses to show the docs page
  the model was actually working from.
