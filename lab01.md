# Labs Overview:

The labs for week 23 will guid you through creating an AI README.md Generator which uses Terraform, Bedrock, and a a series of collaborating agents that will work together to accomplish the shared overall goal.

# Lab 1: Infrastructure Foundation with Terraform

Welcome to the project! Before we can build our AI agents, we need to create a place for them to live and work in the cloud. In this lab, we will use Terraform to build the foundational "scaffolding" for our application on AWS. We'll focus on creating reusable infrastructure components, a core best practice in modern DevOps.

By the end of this lab, you'll have a solid, automated foundation ready for the AI components we'll build next.

## Objectives

You will have:

- A project structure for managing Terraform code.
- A reusable Terraform module for creating S3 buckets.
- A reusable Terraform module for creating IAM roles.
- An S3 bucket and an IAM role deployed in your AWS account.

You will be able to:

- Explain the purpose of a Terraform module.
- Initialize and apply a Terraform configuration.
- Verify the creation of resources in the AWS Management Console.

## 1. Project Setup

First, let's create the directory structure for our project and initialize it.

```bash
# Create the main project folder
mkdir readme-generator
cd readme-generator

# Create the main Terraform file and a directory for our modules
touch main.tf
mkdir modules
```

Your project structure should now look like this:

```
readme-generator/
├── main.tf
└── modules/
```

## 2. Building the S3 Bucket Module

A Terraform module is a reusable package of configuration that lets you create the same type of resource multiple times without repeating code. Our first module will be for creating S3 buckets.

### 2.1: Create the Module Directory

```bash
# Create a directory for our S3 module inside the modules folder
mkdir modules/s3
```

### 2.2: Define the Module's Files

Now, create three files inside the modules/s3 directory.

```bash
touch modules/s3/main.tf modules/s3/variables.tf modules/s3/outputs.tf
```

- **variables.tf:** Defines the inputs our module will accept (like a bucket name).
- **main.tf:** Defines the resources the module will create (the S3 bucket itself).
- **outputs.tf:** Defines the outputs the module will return (like the bucket's ID).

### 2.3: Add the Code

Add the following code to each file.

**modules/s3/variables.tf**

```terraform
variable "bucket_name" {
  description = "The name of the S3 bucket to create."
  type        = string
}
```

**What this does:** It tells Terraform that this module requires a single input variable called `bucket_name`.

**modules/s3/main.tf**

```terraform
resource "aws_s3_bucket" "this" {
  bucket = var.bucket_name
}
```

**What this does:** This is the core logic. It declares an AWS S3 bucket resource and uses the `bucket_name` variable to set its name.

**modules/s3/outputs.tf**

```terraform
output "bucket_id" {
  description = "The ID (name) of the S3 bucket."
  value       = aws_s3_bucket.this.id
}

output "bucket_arn" {
  description = "The ARN of the S3 bucket."
  value       = aws_s3_bucket.this.arn
}
```

**What this does:** After the bucket is created, this makes its ID available to be used by other parts of our code.

## 3. Building the IAM Role Module

Next, we'll create a module for an IAM Role. Our Lambda functions will need this role to get permission to interact with other AWS services like Bedrock.

### 3.1: Create the Module Directory

```bash
# Create a directory for our IAM module
mkdir modules/iam
```

### 3.2: Define the Module's Files

```bash
touch modules/iam/main.tf modules/iam/variables.tf modules/iam/outputs.tf
```

### 3.3: Add the Code

Add the following code to each file.

**modules/iam/variables.tf**

```terraform
variable "role_name" {
  description = "The name of the IAM role."
  type        = string
}

variable "policy_arns" {
  description = "A list of policy ARNs to attach to the role."
  type        = list(string)
  default     = []
}

variable "service_principals" {
  description = "List of AWS services allowed to assume this role."
  type        = list(string)
}
```

**modules/iam/main.tf**

```terraform
resource "aws_iam_role" "this" {
  name = var.role_name

  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{
      Action    = "sts:AssumeRole",
      Effect    = "Allow",
      Principal = {
        Service = var.service_principals
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "this" {
  for_each   = toset(var.policy_arns)
  role       = aws_iam_role.this.name
  policy_arn = each.value
}
```

**What this does:** The first block creates an IAM Role that the Lambda service can "assume." The second block attaches the permission policies that we provide as input.

**modules/iam/outputs.tf**

```terraform
output "role_arn" {
  description = "The ARN of the IAM role."
  value       = aws_iam_role.this.arn
}

output "role_name" {
  description = "The name of the IAM role."
  value       = aws_iam_role.this.name
}
```

## 4. Using the Modules

Now that our modules are built, we can use them in our root main.tf file to create our resources.

Add the following code to your main.tf file in the root directory.

**main.tf**

```terraform
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
}

provider "aws" {
  region = "us-east-1" # You can change this to your preferred region
}

resource "random_string" "suffix" {
  length  = 8
  special = false
  upper   = false
}

module "s3_bucket" {
  source      = "./modules/s3"
  bucket_name = "readme-generator-output-bucket-${random_string.suffix.result}"
}


# Role specifically for the Lambda function to run
module "lambda_execution_role" {
  source             = "./modules/iam"
  role_name          = "ReadmeGeneratorLambdaExecutionRole"
  service_principals = ["lambda.amazonaws.com"]
  policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  ]
}

# Role specifically for the Bedrock Agent to use
module "bedrock_agent_role" {
  source             = "./modules/iam"
  role_name          = "ReadmeGeneratorBedrockAgentRole"
  service_principals = ["bedrock.amazonaws.com"]
  policy_arns = [
    "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
  ]
}

output "readme_bucket_name" {
  description = "The name of the S3 bucket where README files are stored."
  value       = module.s3_bucket.bucket_id
}
```

**What this does:**

- The terraform and provider blocks set up the AWS provider.
- The module `s3_bucket` block calls our S3 module, giving it a unique bucket name.
- The module `lambda_iam_role` block calls our IAM module, giving it a name and attaching the basic permissions our future Lambda functions will need.

## 5. Deployment and Validation

It's time to bring our infrastructure to life! Open your terminal in the root of the readme-generator project.

\*\*\* You should already have:

- AWS CLI installed: if not check out [AWS CLI Instructions](../Resources/aws-cli-install.md)
- Terraform installed: if not check out [Terraform Install](../Resources/terraform-install.md)

### 5.1: Initialize Terraform

```bash
# This command downloads the necessary provider plugins
terraform init
```

### 5.2: Plan the Changes

```bash
# This command shows you what Terraform is about to create
terraform plan
```

Review the output. You should see that Terraform plans to create an S3 bucket and an IAM role.

### 5.3: Apply the Changes

```bash
# This command builds the infrastructure
terraform apply
```

Terraform will ask for confirmation. Type `yes` and press Enter.

### 5.4: Validate in AWS

1. Log in to your AWS Management Console.
2. Navigate to the S3 service. Sort by Creation Date, You should see the new bucket you created `readme-generator-output-bucket-{id}`.
3. Navigate to the IAM service, click on "Roles", use Searchbar and you should see the `ReadmeGeneratorLambdaRole` and `ReadmeGeneratorBedrockAgentRole`.

### 6: Create .gitignore and first commit

```bash
touch .gitignore
```

add to `.gitignore`:

```bash
# Python virtual environment
venv/
.venv/

# Python cache files
__pycache__/
*.pyc

# Terraform state and provider files
# These can contain sensitive information and should not be committed!
.terraform/
*.tfstate
*.tfstate.*.backup

# Build artifacts
# We generate this zip file, so we don't need to store it in git
dist/

# IDE and OS specific files
.vscode/
.idea/
.DS_Store

# Package installers and archives
*.pkg
*.dmg
*.exe
*.zip
*.tar.gz
```

## Conclusion

Congratulations! You have successfully built and deployed the foundational infrastructure for our project using Terraform modules. This is a critical first step in building any cloud-native application.

With this foundation in place, we are now ready for Lab 2, where we will create our first Bedrock Agent and give it a custom tool.