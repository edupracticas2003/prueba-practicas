pipeline {
    agent any

    tools {
        'hudson.plugins.sonar.SonarRunnerInstallation'  'SonarScanner'
    }

    environment {
        REGISTRY_URL   = 'tw-srv-cicd:5000' 
        IMAGE_NAME     = 'app-com-labels'
        
        // 🌟 Usaremos estas dos variables dinámicas abajo
        TAG_GENERIC    = 'staging'
        TAG_VERSION    = "staging-${env.BUILD_NUMBER}"

        REGISTRY_CRED  = 'docker-registry-credentials'
    }

    stages {
        stage('Validar CHANGELOG') {
            steps {
                echo 'Validando CHANGELOG: fecha de hoy (Europe/Madrid) y al menos un Jira en la versión superior.'
                sh 'TZ=Europe/Madrid CHANGELOG_TZ=Europe/Madrid sh scripts-cicd/validate_changelog.sh CHANGELOG.md'
            }
        }

        stage('Análisis SonarQube') {
            steps {
                withSonarQubeEnv('SonarServer') { 
                    script {
                        // 1. Obtenemos dinámicamente la ruta de instalación (/var/jenkins_home/tools/...)
                        def scannerHome = tool name: 'SonarScanner', type: 'hudson.plugins.sonar.SonarRunnerInstallation'
                        
                        // 2. Ejecutamos el binario apuntando directamente a su carpeta 'bin'
                        sh "${scannerHome}/bin/sonar-scanner -Dsonar.projectKey=app-com-labels -Dsonar.sources=."
                    }
                }
            }
        }

        stage('Construir Imagen Docker') {
            steps {
                echo "Construyendo la versión numerada: ${env.TAG_VERSION}..."
                // 1. Construimos la imagen local con el tag que lleva el número de build
                sh 'docker build -t $REGISTRY_URL/$IMAGE_NAME:$TAG_VERSION .'
                
                // 2. Le creamos el alias genérico 'staging' a esa misma imagen
                sh 'docker tag $REGISTRY_URL/$IMAGE_NAME:$TAG_VERSION $REGISTRY_URL/$IMAGE_NAME:$TAG_GENERIC'
            }
        }

        stage('Subir al Docker Registry Privado') {
            steps {
                echo 'Iniciando sesión y subiendo ambas etiquetas...'
                withCredentials([usernamePassword(credentialsId: "${env.REGISTRY_CRED}", 
                                                 usernameVariable: 'REG_USER', 
                                                 passwordVariable: 'REG_PASS')]) {
                    sh 'echo "$REG_PASS" | docker login $REGISTRY_URL -u $REG_USER --password-stdin'
                    
                    // 🌟 3. Subimos la versión específica con número (ej: staging-12)
                    sh 'docker push $REGISTRY_URL/$IMAGE_NAME:$TAG_VERSION'
                    
                    // 🌟 4. Subimos la versión general para actualizar el puntero de staging
                    sh 'docker push $REGISTRY_URL/$IMAGE_NAME:$TAG_GENERIC'
                    
                    sh 'docker logout $REGISTRY_URL'
                }
            }
        }
    }

    post {
        always {
            echo 'Limpiando imágenes locales del servidor de Sonar para no llenar el disco...'
            // 5. Borramos ambas del servidor local para no acumular basura espacial
            sh 'docker rmi $REGISTRY_URL/$IMAGE_NAME:$TAG_VERSION || true'
            sh 'docker rmi $REGISTRY_URL/$IMAGE_NAME:$TAG_GENERIC || true'
        }
    }
}

