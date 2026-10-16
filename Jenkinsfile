pipeline {
    agent any

    parameters {
        booleanParam(
            name: 'DEPLOY_TO_EC2',
            defaultValue: false,
            description: 'Deploy the validated main branch to the configured EC2 host.'
        )
    }

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    environment {
        // CI-safe Django settings. Production secrets belong in Jenkins credentials.
        DJANGO_SECRET_KEY = 'ci-only-not-for-production'
        DJANGO_DEBUG = 'False'
        DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'
        DB_ENGINE = 'django.db.backends.sqlite3'
        DB_NAME = 'db.sqlite3'
        DEPLOY_PATH = '/opt/sportmate/backend'
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
        PYTHONDONTWRITEBYTECODE = '1'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Set up Python') {
            steps {
                sh '''
                    python3 --version
                    python3 -m venv .venv
                    . .venv/bin/activate
                    python -m pip install --upgrade pip
                    python -m pip install -r requirements.txt
                '''
            }
        }

        stage('Validate Django') {
            steps {
                sh '''
                    . .venv/bin/activate
                    python manage.py check
                    python manage.py makemigrations --check --dry-run
                '''
            }
        }

        stage('Test backend') {
            steps {
                sh '''
                    . .venv/bin/activate
                    python manage.py test --verbosity=2
                '''
            }
        }

        stage('Deploy to EC2') {
            when {
                allOf {
                    expression { return params.DEPLOY_TO_EC2 }
                    expression {
                        return env.BRANCH_NAME == 'main' ||
                            env.GIT_BRANCH == 'main' ||
                            env.GIT_BRANCH == 'origin/main'
                    }
                }
            }
            steps {
                sh '''
                    set -eu
                    umask 002

                    # Keep server-only configuration, uploaded media, and generated
                    # static assets out of source control and intact between releases.
                    rsync -a --delete \\
                        --exclude '.env' \\
                        --exclude '.venv/' \\
                        --exclude 'venv/' \\
                        --exclude 'media/' \\
                        --exclude 'staticfiles/' \\
                        --exclude 'db.sqlite3' \\
                        --exclude '.git/' \\
                        ./ "${DEPLOY_PATH}/"

                    cd "${DEPLOY_PATH}"
                    python3 -m venv .venv
                    . .venv/bin/activate
                    python -m pip install --upgrade pip
                    python -m pip install -r requirements.txt

                    # This file exists only on EC2 and must be readable by the
                    # Jenkins deployment user and the sportmate service account.
                    set -a
                    . /etc/sportmate/backend.env
                    set +a
                    python manage.py migrate --noinput
                    python manage.py collectstatic --noinput
                    python manage.py check --deploy

                    sudo systemctl restart sportmate-backend.service
                    sudo systemctl is-active --quiet sportmate-backend.service
                '''
            }
        }
    }

    post {
        always {
            deleteDir()
        }
    }
}
