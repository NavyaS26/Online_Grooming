# SafeGuard AI

A real-time AI-powered child grooming detection and intervention system that monitors online conversations, identifies grooming behaviour using a fine-tuned DistilBERT classifier and LLaMA 3.3-70B, and protects children by automatically intercepting dangerous conversations before exploitation can occur.

---
## 🌐 Live Demo

https://online-grooming.onrender.com

### Demo Instructions

1. Open the application in two browser windows.
2. Use one window as the child and the other as the adult.
3. Start a conversation between the two users.
4. Observe the live grooming risk score and warning indicators.
5. Continue the conversation until the intervention threshold is reached.
6. Watch the AI Sandbox activate and intercept the conversation.
7. Observe parent alerts, evidence capture, and automated conversation termination.
## What It Does

* Monitors conversations in real time and evaluates each incoming message for grooming risk
* Displays live risk indicators and warning levels as suspicious behaviour escalates
* Uses a fine-tuned DistilBERT model to classify grooming-related messages
* Enhances detection with LLaMA 3.3-70B contextual analysis and explanation generation
* Automatically activates an AI Sandbox when risk exceeds a predefined threshold
* Intercepts both sides of the conversation without alerting the predator
* Sends immediate parent notifications and preserves conversation evidence
* Terminates dangerous conversations through natural AI-generated exits
* Provides post-conversation support and safety guidance to the child

---

## Tech Stack

| Layer               | Technology                                    |
| ------------------- | --------------------------------------------- |
| Backend             | Python, Flask, Flask-SocketIO                 |
| Async Processing    | Gevent, Gevent-WebSocket                      |
| Production Server   | Gunicorn                                      |
| Grooming Detection  | DistilBERT, HuggingFace Transformers, PyTorch |
| Contextual Analysis | LLaMA 3.3-70B via Groq API                    |
| Fallback Detection  | Rule-Based Regex Engine                       |
| Notifications       | Resend API                                    |
| Frontend            | HTML, CSS, JavaScript                         |
| Deployment          | Render                                        |

---

## Project Structure

```text
safeguard-ai/
├── app.py
├── safeguard_detector.py
├── train_model.py
├── safeguard_model/
├── requirements.txt
├── render.yaml
├── .env
└── templates/
    └── chat.html
```

---

## How It Works

### Detection

Every message is analyzed by a fine-tuned DistilBERT model that produces a grooming risk score between 0 and 1. Contextual understanding is provided by LLaMA 3.3-70B, which examines recent conversation history to identify manipulation patterns, coercion attempts, secrecy requests, and exploitation indicators.

### Risk Assessment

The system continuously tracks cumulative grooming risk through multiple stages:

**Friendly Interaction → Suspicious Behaviour → Escalating Risk → High Risk → Exploitation Attempt**

Intervention is triggered only after sustained grooming behaviour is detected, reducing false positives.

### AI Sandbox

Once the intervention threshold is reached, both participants are silently moved into an AI-controlled sandbox.

* The predator receives AI-generated responses appearing to come from the child.
* The child receives AI-generated responses appearing to come from the predator.
* Neither participant is informed that the conversation has been intercepted.

### Parent Notification

The system immediately:

* Sends an alert email to the parent or guardian
* Stores conversation evidence
* Records grooming indicators and risk scores
* Generates a safety report for review

### Safe Conversation Termination

After several controlled exchanges, the AI gradually ends the conversation naturally.

The child receives safety resources and guidance, while the predator only sees a standard disconnection message.

---

## Key Features

* Real-time grooming detection
* DistilBERT-based risk classification
* LLM-powered contextual analysis
* AI Sandbox intervention mechanism
* Parent alert system
* Evidence preservation
* Risk visualization dashboard
* Child support chatbot
* WebSocket-based real-time communication
* Automated conversation termination

---

## Architecture

```text
Chat Messages
      ↓
DistilBERT Risk Classifier
      ↓
LLaMA Context Analysis
      ↓
Risk Scoring Engine
      ↓
Threshold Detection
      ↓
AI Sandbox Activation
      ↓
Parent Alert + Evidence Capture
      ↓
Safe Conversation Termination
```

---

## Impact

SafeGuard AI addresses one of the fastest-growing threats facing children online: grooming and exploitation through digital communication platforms.

By combining machine learning, large language models, and real-time intervention mechanisms, the system aims to detect harmful interactions early and protect vulnerable users before exploitation occurs.

---

## Disclaimer

This project is a research prototype developed for educational and demonstration purposes. It is intended to showcase the feasibility of AI-assisted child safety systems and should not be considered a replacement for parental supervision, professional safeguarding practices, or law enforcement intervention.

---

