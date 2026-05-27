# Git Remote and Authentication Setup

## 1. Generate SSH Key (in WSL)
Run this in your terminal:
```bash
ssh-keygen -t ed25519 -C "your_email@example.com"
```
Press Enter to accept the default file location and choose a passphrase (or leave it empty).

## 2. Add Key to GitHub
1. Copy your public key:
   ```bash
   cat ~/.ssh/id_ed25519.pub
   ```
2. Go to GitHub -> Settings -> **SSH and GPG keys** -> **New SSH key**.
3. Paste the content and save.

## 3. Create the Repository on GitHub
1. Go to GitHub and create a new repository named `codingAssitant`.
2. **Do not** initialize it with a README or license (since you already have them locally).

## 4. Link Local to Remote
Run these commands in your project root:
```bash
# Remove egg-info from git tracking (locally)
git rm -r --cached src/ai_tech_lead.egg-info
git add .gitignore
git commit -m "chore: remove build artifacts and update gitignore"

# Link the remote using the SSH URL
# Replace USERNAME with your actual GitHub username
git remote add origin git@github.com:USERNAME/ai-tech-lead.git

# Set the main branch and push
git branch -M main
git push -u origin main
```

## Verification
Run `git remote -v`. It should show the `origin` pointing to the `git@github.com` address.
