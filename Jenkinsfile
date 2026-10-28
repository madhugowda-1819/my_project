pipeline {
    agent any

    parameters {
        booleanParam(
            name: 'DEPLOY_TO_EC2',
            defaultValue: true,
            description: 'Deploy validated main branch to AWS EC2 instance.'
        )
        string(
            name: 'EC2_HOST',
            defaultValue: '127.0.0.1',
            description: 'EC2 public DNS name or private IP reachable by this Jenkins agent.'
        )
    }

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    environment {
        // CI validation settings (SQLite for local unit tests only)
        DJANGO_SECRET_KEY = 'ci-only-not-for-production'
        DJANGO_DEBUG = 'False'
        DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'
        DB_ENGINE = 'django.db.backends.sqlite3'
        DB_NAME = 'db.sqlite3'

        // Live EC2 Target Paths & Service
        REMOTE_HOST = "${params.EC2_HOST}"
        REMOTE_USER       = 'ubuntu'
        REMOTE_DEPLOY_DIR = '/home/ubuntu/my_project/my_project'
        SERVICE_NAME      = 'gunicorn.service'
        NGINX_SERVICE_NAME = 'nginx.service'

        PIP_DISABLE_PIP_VERSION_CHECK = '1'
        PYTHONDONTWRITEBYTECODE = '1'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scmGit(
                    branches: [[name: 'main']],
                    userRemoteConfigs: [[
                        url: 'https://github.com/madhugowda-1819/my_project.git',
                        credentialsId: 'GITHUB_CREDENTIALS_ID'
                    ]]
                )
            }
        }

        stage('Setup CI') {
            steps {
                sh '''
                    python3 --version
                    python3 -m venv .ci_venv
                    .ci_venv/bin/python -m pip install -r requirements.txt
                '''
            }
        }

        stage('Run Django Checks') {
            steps {
                sh '''
                    set -eu
                    .ci_venv/bin/python manage.py check
                    .ci_venv/bin/python manage.py makemigrations --check --dry-run
                '''
            }
        }

        stage('Run Django Tests') {
            steps {
                sh '''
                    set -eu
                    .ci_venv/bin/python manage.py test --verbosity=2
                '''
            }
        }

        stage('Deploy to EC2 via SSH') {
            when {
                allOf {
                    expression { return params.DEPLOY_TO_EC2 }
                    expression {
                        return (env.BRANCH_NAME ?: env.GIT_BRANCH ?: '') == 'main' ||
                               (env.GIT_BRANCH ?: '') == 'origin/main'
                    }
                }
            }
            steps {
                script {
                    withCredentials([
                        sshUserPrivateKey(credentialsId: 'EC2_SSH_KEY', keyFileVariable: 'SSH_KEY_FILE', usernameVariable: 'SSH_USER'),
                        file(credentialsId: 'PROD_ENV_SECRET_FILE', variable: 'PROD_ENV_FILE')
                    ]) {
                        sh """
                            set -eu

                            # 1. Sync project files to EC2 (preserving live .env and media)
                            rsync -avz --delete \
                                -e "ssh -i ${SSH_KEY_FILE} -o StrictHostKeyChecking=no" \
                                --exclude '.env' \
                                --exclude 'venv/' \
                                --exclude '.venv/' \
                                --exclude '.ci_venv/' \
                                --exclude 'media/' \
                                --exclude 'staticfiles/' \
                                --exclude 'db.sqlite3' \
                                --exclude '.git/' \
                                ./ ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DEPLOY_DIR}/

                            # 2. Inject fresh production .env secret file
                            scp -i ${SSH_KEY_FILE} -o StrictHostKeyChecking=no ${PROD_ENV_FILE} ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DEPLOY_DIR}/.env

                            # 3. Execute remote setup
                            ssh -i ${SSH_KEY_FILE} -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} "
                                set -eu
                                cd ${REMOTE_DEPLOY_DIR}

                                # Lock down .env permissions
                                chmod 600 .env

                                # Install dependencies in the project virtual environment.
                                # Do not modify Debian's system-managed pip.
                                python3 -m venv .venv
                                .venv/bin/python -m pip install -r requirements.txt
                                .venv/bin/python -m pip install gunicorn mysqlclient django python-dotenv

                                # Run database migrations and static collection
                                .venv/bin/python manage.py migrate --noinput
                                .venv/bin/python manage.py collectstatic --noinput

                                # Install and enable the hourly live-ground cache cleanup.
                                # The installer uses the deployed virtualenv and the SSH user
                                # so no system Python or password prompt is required.
                                chmod +x deployment/linux/install_ground_cache_cleanup.sh
                                sudo deployment/linux/install_ground_cache_cleanup.sh "${REMOTE_DEPLOY_DIR}" "${REMOTE_DEPLOY_DIR}/.venv" "${REMOTE_USER}"
                                sudo systemctl start sportmate-ground-cache-cleanup.service
                                sudo systemctl is-enabled --quiet sportmate-ground-cache-cleanup.timer
                                sudo systemctl is-active --quiet sportmate-ground-cache-cleanup.timer

                            "
                        """
                    }
                }
            }
        }

        stage('Restart Gunicorn and Nginx Services') {
            when {
                allOf {
                    expression { return params.DEPLOY_TO_EC2 }
                    expression {
                        return (env.BRANCH_NAME ?: env.GIT_BRANCH ?: '') == 'main' ||
                               (env.GIT_BRANCH ?: '') == 'origin/main'
                    }
                }
            }
            steps {
                script {
                    withCredentials([
                        sshUserPrivateKey(credentialsId: 'EC2_SSH_KEY', keyFileVariable: 'SSH_KEY_FILE', usernameVariable: 'SSH_USER')
                    ]) {
                        sh '''
                            set -eu
                            ssh -i "$SSH_KEY_FILE" -o StrictHostKeyChecking=no "$REMOTE_USER@$REMOTE_HOST" \
                                "sudo systemctl restart '$SERVICE_NAME' && sudo systemctl is-active --quiet '$SERVICE_NAME' && sudo systemctl restart '$NGINX_SERVICE_NAME' && sudo systemctl is-active --quiet '$NGINX_SERVICE_NAME'"
                        '''
                    }
                }
            }
        }
    }

    post {
        failure {
            echo "Pipeline failed! Check stage output above."
        }
        success {
            echo "Deployed and synced to EC2 MySQL backend successfully!"
        }
        always {
            cleanWs disableDeferredWipeout: true, deleteDirs: true
        }
    }
}
