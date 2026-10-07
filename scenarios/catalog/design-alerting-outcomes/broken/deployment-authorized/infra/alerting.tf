resource "aws_cloudwatch_metric_alarm" "orders_dlq_age" {
  alarm_name = "orders-dlq-age"
}
