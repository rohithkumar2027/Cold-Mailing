"""
Cold Email Templates & Generator Module
Provides tailored, high-converting cold email drafts for:
- Software Engineering (SWE) Internships
- AI / Machine Learning (ML) Internships
- Data Science Internships
"""
from typing import Dict, Optional

TEMPLATES = {
    "swe": {
        "subject": "Internship Inquiry: Software Engineering Intern - {candidate_name}",
        "body": """Hi {first_name},

I hope you're having a great week!

I came across your profile while exploring engineering opportunities at {company}. I’ve been closely following {company}’s work in tech and would love to bring my software engineering background to your team as an intern.

A brief snapshot of my technical background:
• Strong foundation in Data Structures, Algorithms, and System Design.
• Built full-stack and backend systems using Python, TypeScript/JavaScript, and cloud services (Docker, REST APIs, PostgreSQL).
• Passionate about building robust, scalable products that impact millions of users.

I would love to learn more about upcoming internship opportunities for Software Engineering at {company}. Are you open to a brief 10-minute chat or could you point me to the right recruiter on your university/early talent team?

My resume and GitHub are linked below for your reference. Thank you so much for your time!

Best regards,
{candidate_name}
{portfolio_or_github}
{linkedin_url}
""",
    },
    "ai_ml": {
        "subject": "Internship Inquiry: AI / Machine Learning Intern - {candidate_name}",
        "body": """Hi {first_name},

I hope this email finds you well!

I noticed your work recruiting top technical talent at {company} and wanted to reach out regarding AI / Machine Learning internship opportunities. I am particularly impressed by {company}’s innovative applications of machine learning and modern AI architectures.

Here is a quick overview of what I bring to the table:
• Experience building, training, and fine-tuning ML models using PyTorch, Hugging Face, and scikit-learn.
• Hands-on project work in Large Language Models (LLMs), Computer Vision / NLP, and ML system deployment.
• Solid fundamentals in probability, linear algebra, and software development practices.

I would be thrilled to contribute to the AI/ML initiatives at {company}. Would you be available for a quick 10-minute conversation this week or next to discuss potential internship openings?

I’ve attached my resume and project portfolio below. Thank you very much for your consideration!

Best regards,
{candidate_name}
{portfolio_or_github}
{linkedin_url}
""",
    },
    "data_science": {
        "subject": "Internship Inquiry: Data Science Intern - {candidate_name}",
        "body": """Hi {first_name},

I hope your week is off to a great start!

I’m reaching out to express my strong interest in Data Science internship roles at {company}. I’ve been fascinated by how {company} leverages data-driven insights and predictive modeling to solve complex problems.

A quick summary of my background:
• Proficient in Python, SQL, pandas, statistical modeling, and data visualization (Tableau / PowerBI).
• Experience conducting end-to-end data analysis: data cleaning, EDA, hypothesis testing, and building predictive models.
• Passionate about translating raw data into actionable product and business strategies.

I would love the opportunity to connect briefly or be considered for upcoming internship positions on your data team. Would you have 10 minutes for a quick chat?

Thank you for your time and guidance, and I look forward to connecting!

Best regards,
{candidate_name}
{portfolio_or_github}
{linkedin_url}
""",
    },
}


def generate_email_draft(
    recruiter_name: str,
    company: str,
    role_category: str = "swe",
    candidate_name: str = "[Your Name]",
    portfolio_or_github: str = "[GitHub / Portfolio Link]",
    candidate_linkedin: str = "[Your LinkedIn Profile]",
) -> Dict[str, str]:
    """
    Generates a personalized cold email draft for a specific recruiter and role.
    """
    first_name = recruiter_name.split()[0] if recruiter_name else "there"
    first_name = first_name.capitalize()

    category_key = role_category.lower()
    template_data = TEMPLATES.get(category_key, TEMPLATES["swe"])

    subject = template_data["subject"].format(
        candidate_name=candidate_name,
        company=company,
        first_name=first_name,
    )

    body = template_data["body"].format(
        first_name=first_name,
        company=company,
        candidate_name=candidate_name,
        portfolio_or_github=portfolio_or_github,
        linkedin_url=candidate_linkedin,
    )

    return {
        "subject": subject,
        "body": body,
    }
