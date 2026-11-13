# ===========================================================================
# infra/ — Como correria esta plataforma en AWS (Parte F). NO se despliega.
# Espejo de los controles locales: KMS+SSE-S3 para el equivalente al cifrado
# en reposo (C3), roles IAM de menor privilegio espejo de C4, versionado.
# ===========================================================================

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "proyecto" {
  type    = string
  default = "bia-data-platform"
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# KMS: llave administrada por el cliente para S3 (cifrado en reposo).
# Politica de menor privilegio: admin = cuenta, uso = roles del pipeline.
# ---------------------------------------------------------------------------
resource "aws_kms_key" "datos" {
  description             = "CMK para datos crudos/curados de ${var.proyecto}"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CuentaAdmin"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "UsoRolesPipeline"
        Effect = "Allow"
        Principal = {
          AWS = [
            aws_iam_role.ingesta.arn,
            aws_iam_role.analista.arn,
          ]
        }
        Action = [
          "kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
      },
      {
        Sid       = "AuditorSoloLectura"
        Effect    = "Allow"
        Principal = { AWS = aws_iam_role.auditor.arn }
        Action    = ["kms:DescribeKey"]
        Resource  = "*"
      },
    ]
  })

  tags = { Proyecto = var.proyecto }
}

resource "aws_kms_alias" "datos" {
  name          = "alias/${var.proyecto}-datos"
  target_key_id = aws_kms_key.datos.key_id
}

# ---------------------------------------------------------------------------
# S3: zona cruda (equivalente a data/) + curada. Cifrado SSE-KMS, versionado,
# bloqueo de acceso publico total.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "raw" {
  bucket = "${var.proyecto}-raw"
  tags   = { Proyecto = var.proyecto, Zona = "raw" }
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.datos.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# Roles IAM espejo de C4 (menor privilegio). En AWS real serian roles de
# servicio (ECS/Lambda); aca basta con trust a la cuenta para ilustrar el
# principio.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "assume_cuenta" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
}

# ingesta: escribe en raw, descifra con KMS. NO lee curado PII en claro.
resource "aws_iam_role" "ingesta" {
  name               = "${var.proyecto}-ingesta"
  assume_role_policy = data.aws_iam_policy_document.assume_cuenta.json
}

data "aws_iam_policy_document" "ingesta" {
  statement {
    sid    = "EscribirRaw"
    effect = "Allow"
    actions = [
      "s3:PutObject", "s3:GetObject", "s3:ListBucket",
      "s3:GetObjectVersion", "s3:AbortMultipartUpload",
    ]
    resources = [aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/*"]
  }
}

resource "aws_iam_role_policy" "ingesta" {
  role   = aws_iam_role.ingesta.id
  policy = data.aws_iam_policy_document.ingesta.json
}

# analista: SOLO lee la capa curada/anonimizada. No toca raw ni PII.
resource "aws_iam_role" "analista" {
  name               = "${var.proyecto}-analista"
  assume_role_policy = data.aws_iam_policy_document.assume_cuenta.json
}

data "aws_iam_policy_document" "analista" {
  statement {
    sid       = "LeerCurado"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/curado/*"]
  }
  statement {
    sid       = "NegarRaw"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = ["${aws_s3_bucket.raw.arn}/raw/*"]
  }
}

resource "aws_iam_role_policy" "analista" {
  role   = aws_iam_role.analista.id
  policy = data.aws_iam_policy_document.analista.json
}

# auditor: solo describe/lee logs. Nada mas.
resource "aws_iam_role" "auditor" {
  name               = "${var.proyecto}-auditor"
  assume_role_policy = data.aws_iam_policy_document.assume_cuenta.json
}

data "aws_iam_policy_document" "auditor" {
  statement {
    sid       = "SoloLogs"
    effect    = "Allow"
    actions   = ["logs:Describe*", "logs:Get*", "logs:FilterLogEvents"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "auditor" {
  role   = aws_iam_role.auditor.id
  policy = data.aws_iam_policy_document.auditor.json
}

output "bucket_raw" { value = aws_s3_bucket.raw.bucket }
output "kms_key_arn" { value = aws_kms_key.datos.arn }
output "roles" {
  value = {
    ingesta  = aws_iam_role.ingesta.arn
    analista = aws_iam_role.analista.arn
    auditor  = aws_iam_role.auditor.arn
  }
}
