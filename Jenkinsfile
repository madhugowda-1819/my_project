pipeline {
    agent any

    parameters {
        booleanParam(
            name: 'DEPLOY_TO_EC2',
            defaultValue: true,
            description: 'Deploy validated main branch to AWS EC2 instance.'
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
        REMOTE_HOST       = '35.173.184.45'
        REMOTE_USER       = 'ubuntu'
        REMOTE_DEPLOY_DIR = '/home/ubuntu/my_project/my_project'
        SERVICE_NAME      = 'gunicorn.service'

        PIP_DISABLE_PIP_VERSION_CHECK = '1'
        PYTHONDONTWRITEBYTECODE = '1'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scmGit(
                    branches: [[name: '*/main']],
                    userRemoteConfigs: [[
                        url: 'https://github.com',
                        credentialsId: 'GITHUB_CREDENTIALS_ID'
                    ]]
                )
            }
        }

        stage('Set up CI & Test') {
            steps {
                sh '''
                    python3 --version
                    python3 -m venv .ci_venv
                    . .ci_venv/bin/activate
                    python -m pip install --upgrade pip
                    python -m pip install -r requirements.txt
                    python manage.py check
                    python manage.py test --verbosity=2
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

                            # 3. Execute remote setup and restart service
                            ssh -i ${SSH_KEY_FILE} -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} "
                                set -eu
                                cd ${REMOTE_DEPLOY_DIR}

                                # Lock down .env permissions
                                chmod 600 .env

                                # Install/update global dependencies
                                sudo pip3 install --upgrade pip --break-system-packages
                                sudo pip3 install -r requirements.txt gunicorn mysqlclient django python-dotenv --break-system-packages

                                # Run database migrations and static collection
                                python3 manage.py migrate --noinput
                                python3 manage.py collectstatic --noinput

                                # Restart systemd gunicorn
                                sudo systemctl restart ${SERVICE_NAME}
                                sudo systemctl is-active --quiet ${SERVICE_NAME}
                            "
                        """
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
