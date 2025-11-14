#!/bin/bash

# Azure Web App startup script for Disgram
echo "Starting Disgram application setup..."

# Check if Git is available, try to install if not
if ! command -v git &> /dev/null; then
    echo "Git not found, attempting to install..."
    
    # Try to install git using available package managers
    if command -v apt-get &> /dev/null; then
        echo "Installing Git with apt-get..."
        apt-get update -qq && apt-get install -y git
    elif command -v apk &> /dev/null; then
        echo "Installing Git with apk..."
        apk update && apk add git
    elif command -v yum &> /dev/null; then
        echo "Installing Git with yum..."
        yum install -y git
    else
        echo "Warning: Could not install Git - package manager not found"
        echo "Application will continue without Git functionality"
    fi
fi

    # Verify and configure Git if available
if command -v git &> /dev/null; then
    echo "Git available: $(git --version)"
    
    # Configure Git globally first
    DEFAULT_BRANCH=${GITHUB_DEPLOY_BRANCH:-azure-prod}
    git config --global user.name "Disgram Bot" 2>/dev/null || true
    git config --global user.email "disgram@bot.local" 2>/dev/null || true
    git config --global init.defaultBranch "$DEFAULT_BRANCH" 2>/dev/null || true
    git config --global pull.rebase false 2>/dev/null || true    # Configure token-based authentication if token is available
    if [ ! -z "$GITHUB_TOKEN" ]; then
        echo "Configuring Git authentication with GitHub token..."
        git config --global credential.helper "!f() { echo \"username=\$GITHUB_TOKEN\"; echo \"password=\"; }; f" 2>/dev/null || true
    fi
    
    # Check if we're in a Git repository
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "No Git repository found - initializing repository..."
        
        # Initialize Git repository
        git init .
        
        # Initialize as fresh repository first
        echo "Initializing fresh Git repository..."
        
        # Add GitHub remote if repository URL is available
        if [ ! -z "$GITHUB_REPO_URL" ]; then
            echo "Adding GitHub remote (clean URL)..."
            git remote add origin "$GITHUB_REPO_URL"
            
            # Try to fetch and sync with remote repository
            DEPLOY_BRANCH=${GITHUB_DEPLOY_BRANCH:-azure-prod}
            echo "Attempting to sync with remote ${DEPLOY_BRANCH} branch..."
            
            # Try to fetch the remote branch
            if git fetch origin "$DEPLOY_BRANCH" 2>/dev/null; then
                echo "Remote branch $DEPLOY_BRANCH found, syncing..."
                
                # Reset the local repository to match remote exactly
                git reset --hard "origin/$DEPLOY_BRANCH" 2>/dev/null || true
                git checkout -B "$DEPLOY_BRANCH" 2>/dev/null || true
                
                # Set upstream tracking
                git branch --set-upstream-to="origin/$DEPLOY_BRANCH" "$DEPLOY_BRANCH" 2>/dev/null || true
                
                echo "Successfully synced with remote $DEPLOY_BRANCH branch"
            else
                echo "Remote branch $DEPLOY_BRANCH not found, will create it..."
                
                # Create the branch locally and add current files
                git checkout -b "$DEPLOY_BRANCH" 2>/dev/null || true
                
                # Add current application files
                git add . 2>/dev/null || true
                git commit -m "Initial commit from Azure deployment" 2>/dev/null || true
                
                # Try to push to create remote branch
                if [ ! -z "$GITHUB_TOKEN" ]; then
                    echo "Creating remote branch..."
                    git push --set-upstream origin "$DEPLOY_BRANCH" 2>/dev/null || echo "Could not create remote branch (will work locally)"
                fi
            fi
        else
            echo "No GITHUB_REPO_URL found - Git operations will be local only"
            
            # Just add current files for local tracking
            git add . 2>/dev/null || true
            git commit -m "Initial local commit" 2>/dev/null || true
        fi
        
        echo "Git repository initialized successfully"
    else
        echo "Git repository already exists"
        
        # Ensure we're on the correct branch (not detached HEAD)
        DEPLOY_BRANCH=${GITHUB_DEPLOY_BRANCH:-azure-prod}
        CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "")
        
        if [ -z "$CURRENT_BRANCH" ]; then
            echo "Detached HEAD detected, checking out branch $DEPLOY_BRANCH..."
            git checkout -B "$DEPLOY_BRANCH" 2>/dev/null || true
            git branch --set-upstream-to="origin/$DEPLOY_BRANCH" "$DEPLOY_BRANCH" 2>/dev/null || true
        elif [ "$CURRENT_BRANCH" != "$DEPLOY_BRANCH" ]; then
            echo "Switching from $CURRENT_BRANCH to $DEPLOY_BRANCH..."
            git checkout "$DEPLOY_BRANCH" 2>/dev/null || git checkout -B "$DEPLOY_BRANCH" 2>/dev/null || true
            git branch --set-upstream-to="origin/$DEPLOY_BRANCH" "$DEPLOY_BRANCH" 2>/dev/null || true
        else
            echo "Already on branch $DEPLOY_BRANCH"
        fi
    fi
else
    echo "Warning: Git not available - Git commit functionality will be disabled"
fi

# Start the Python application
echo "Starting Disgram Python application..."
exec python main.py