const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, AlignmentType,
  BorderStyle, LevelFormat, convertInchesToTwip
} = require('docx');

const FONT = 'Calibri';

/* ---------------- shared paragraph helpers ---------------- */
const name = (t) => new Paragraph({ spacing:{after:40},
  children:[new TextRun({text:t,bold:true,size:30,font:FONT,characterSpacing:20})]});
const contact = (t) => new Paragraph({ spacing:{after:120},
  children:[new TextRun({text:t,size:19,font:FONT})]});
const headline = (t) => new Paragraph({ spacing:{after:160},
  children:[new TextRun({text:t,bold:true,size:21,font:FONT})]});
const section = (t) => new Paragraph({ spacing:{before:220,after:100},
  border:{bottom:{style:BorderStyle.SINGLE,size:6,space:2,color:'444444'}},
  children:[new TextRun({text:t,bold:true,size:21,font:FONT,characterSpacing:12})]});
const body = (t,o={}) => new Paragraph({ spacing:{after:o.after===undefined?100:o.after},
  children:[new TextRun({text:t,size:20,font:FONT})]});
const skillLine = (l,c) => new Paragraph({ spacing:{after:70}, children:[
  new TextRun({text:l+': ',bold:true,size:20,font:FONT}),
  new TextRun({text:c,size:20,font:FONT})]});
const employer = (org,loc,dates) => new Paragraph({ spacing:{before:180,after:20},
  tabStops:[{type:'right',position:10080}], children:[
  new TextRun({text:org,bold:true,size:21,font:FONT}),
  new TextRun({text:'  |  '+loc,size:20,font:FONT}),
  new TextRun({text:'\t'+dates,size:20,font:FONT})]});
const title = (t) => new Paragraph({ spacing:{after:60},
  children:[new TextRun({text:t,italics:true,bold:true,size:20,font:FONT})]});
const scope = (t) => new Paragraph({ spacing:{after:80},
  children:[new TextRun({text:t,size:20,font:FONT})]});
const bullet = (t) => new Paragraph({ numbering:{reference:'bullets',level:0},
  spacing:{after:60}, children:[new TextRun({text:t,size:20,font:FONT})]});

const HEAD = [
  name('JAMES A. BEAM, MBA'),
  contact('Albany, OR  |  714-322-9914  |  james@beamlegacy.com  |  linkedin.com/in/jamesabeam  |  beamlegacy.com'),
];

const EDUCATION = [
  section('EDUCATION'),
  body('Master of Business Administration \u2014 Concordia University Irvine, 2017',{after:40}),
  body('Bachelor of Science, Business Administration (Entrepreneurship) \u2014 Chapman University, 2011',{after:0}),
];

/* ================= VARIANT L — LEADERSHIP / ENABLEMENT ================= */
const LEADER = [
  ...HEAD,
  headline('AI Enablement & Operations  |  Workflow Automation  |  Business Operations'),

  section('PROFESSIONAL SUMMARY'),
  body('Operations leader who designs, builds, and ships AI-automated business systems. Scaled a consumer packaged goods brand from $250K to $12M in twelve months by building the operational, financial, and compliance infrastructure behind it, then built and shipped AI-native software platforms from scratch \u2014 including an autonomous decision-automation system running live in production. Combines 12+ years of cross-functional operations leadership with hands-on technical implementation: agentic workflow design, workflow redesign, process automation, adoption and change management, and measurable time-and-cost reduction. MBA. Remote-first operator.',{after:40}),

  section('CORE COMPETENCIES'),
  body('AI Enablement & Adoption  \u2022  AI Workflow Automation  \u2022  Process Optimization  \u2022  Business Operations  \u2022  Cross-Functional Collaboration  \u2022  Change Management  \u2022  Program & Project Management  \u2022  Product Ownership  \u2022  Data & Business Intelligence  \u2022  Financial Planning & Analysis  \u2022  Operational Scaling  \u2022  SOP & Playbook Design  \u2022  Stakeholder & Vendor Management  \u2022  Team Leadership',{after:40}),

  section('TECHNICAL SKILLS'),
  skillLine('AI & Automation','Anthropic Claude API (Batch API, prompt caching, Model Context Protocol), Claude Code, multi-agent orchestration, agentic workflow design, prompt engineering, LLM evaluation, n8n, Make, Zapier, Microsoft Power Automate'),
  skillLine('Data & Development','Python, SQL, REST API integration, Flask, React, JavaScript, Git / GitHub, Railway, data pipelines, vector embeddings'),
  skillLine('Business Systems','QuickBooks, NetSuite, Shopify, Klaviyo, Asana, Slack, Google Workspace, Microsoft 365, Excel / Google Sheets modeling'),
  skillLine('Methodologies','Agile, Scrum, PMO governance, Sales & Operations Planning (S&OP), process mapping, statistical validation (walk-forward, Monte Carlo)'),

  section('PROFESSIONAL EXPERIENCE'),

  employer('BEAM LEGACY GROUP','Albany, OR (Remote)','2025 \u2013 Present'),
  title('Founder & Principal Operator'),
  scope('Bootstrapped holding company building AI-native ventures. Specify, build, and ship production software using AI coding agents \u2014 operating as product owner, operator, and implementer across the portfolio.'),
  bullet('Specify, build, and deploy production-grade platforms as a single operator by directing AI coding agents \u2014 covering architecture, implementation, deployment, and iteration on work that would conventionally require a multi-person engineering team.'),
  bullet('Architected and launched an autonomous forecasting and decision-automation platform now running live in production \u2014 68 API endpoints, 540 configurable strategy profiles, ensemble modeling, and automated risk controls with real-time hedging.'),
  bullet('Designed the statistical validation layer governing production deployment: walk-forward optimization, 10,000-iteration Monte Carlo simulation, Bayesian estimation, Kelly-criterion position sizing, and drawdown and fee-sensitivity analysis.'),
  bullet('Led product design and go-to-market for an AI platform for small-business owners (in beta) that consolidates search visibility, content production, and cross-platform business intelligence \u2014 replacing an estimated $3,700\u2013$9,500 per month of agency and freelance labor at a $49 per month price point.'),
  bullet('Owned full product specification, scoring methodology, and pricing architecture; audited the platform\u2019s own scoring engine and remediated 12 defects across three priority tiers before customer release.'),
  bullet('Took a consumer product from CAD design to launch-ready: supplier cost modeling across four vendors, component sourcing, direct-to-consumer storefront, and lifecycle email automation.'),

  employer('COMARKCO','Los Angeles, CA','2023 \u2013 2025'),
  title('Chief of Staff  |  Fractional COO / CFO'),
  scope('Served as fractional COO/CFO across a portfolio of consumer packaged goods (CPG) brands, building a repeatable operating model covering customer experience, logistics, HR, IT, legal and compliance, data analytics, bookkeeping, and tax.'),
  bullet('Built the operational, financial, and compliance infrastructure that scaled top-line revenue from $250K to $12M in a single year, with $14M projected the following year.'),
  bullet('Secured $3M in working capital, inventory financing, and revolving credit to fund production and market entry, preventing supply interruption through peak demand.'),
  bullet('Designed and executed international expansion strategy across the Americas, Europe, Asia, and Oceania; launched Australia and Canada and established regulatory readiness for 20+ countries.'),
  bullet('Managed intellectual property, tax compliance, product safety certification, and import/export logistics for entry into regulated markets.'),
  bullet('Scaled a cross-functional team from lean startup staff to 25 employees, improving retention and cross-department accountability.'),
  bullet('Standardized and automated recurring finance, logistics, and reporting workflows, cutting manual processing time and removing bottlenecks across simultaneous multi-country operations.'),

  employer('WORKSITE LABS','Los Angeles, CA','2020 \u2013 2023'),
  title('SVP of Experience & Product'),
  scope('Led customer experience and product strategy through hyper-growth from $0 to $25M in revenue in year one.'),
  bullet('Acted as Product Owner for the digital patient booking and reservation platform, owning requirements definition, user experience design, and cross-functional sprint planning.'),
  bullet('Established the Project Management Office (PMO), creating governance and prioritization frameworks that standardized execution of cross-functional initiatives company-wide.'),
  bullet('Developed and executed a company-wide customer experience strategy, improving CSAT and NPS scores and reducing churn.'),
  bullet('Built partner onboarding frameworks that accelerated implementation timelines and standardized service quality across accounts.'),
  bullet('Designed and implemented the data infrastructure capturing customer feedback and engagement trends to inform executive decision-making.'),

  employer('STAIRCASE DIGITAL / WHEELHOUSE STUDIO','Los Angeles, CA','2018 \u2013 2020'),
  title('Chief of Staff  |  Director of Operations'),
  scope('Strategic partner to executives across operations, fundraising, and digital product; led development of MyIPO, a Reg A+ digital securities offering platform, and Next Ones, an athlete discovery platform.'),
  bullet('Managed a cross-functional engineering team using Agile and Scrum methodologies to deliver on-time, high-quality releases.'),
  bullet('Built analytics and tracking systems for a proprietary advertising methodology, improving campaign targeting and performance.'),
  bullet('Automated ad-creation workflows with real-time analytics feedback, reducing iteration time on campaign production.'),
  bullet('Produced financial models and investor materials that supported successful fundraising efforts.'),

  employer('UNIVERSITY OF CALIFORNIA, IRVINE','Irvine, CA','2013 \u2013 2018'),
  title('Operations Analyst, Civil & Environmental Engineering'),
  bullet('Administered budgets for multiple research grants totaling $3.4M, ensuring funding compliance and accurate reporting.'),
  bullet('Modernized departmental processes, reducing administrative turnaround time and operating cost.'),
  bullet('Built an alumni engagement program that strengthened department relationships and increased donation activity.'),

  ...EDUCATION,
];

/* ================= VARIANT E — ENGINEER / IC ================= */
const ENGINEER = [
  ...HEAD,
  headline('AI & Automation Engineer  |  Workflow Automation  |  Systems Integration'),

  section('PROFESSIONAL SUMMARY'),
  body('Builds and ships AI automation systems end to end \u2014 specification through production. Designed and deployed an autonomous decision-automation platform running live in production across 68 API endpoints with ensemble modeling and automated risk controls, plus an AI platform for small-business owners currently in beta. Twelve years of operations experience underneath the technical work, which means the automations get built against how a business actually runs rather than against an idealized process diagram. Seeking hands-on build work as an individual contributor rather than people management. Remote-first.',{after:40}),

  section('TECHNICAL SKILLS'),
  skillLine('AI & Automation','Anthropic Claude API (Batch API, prompt caching, Model Context Protocol), Claude Code, multi-agent orchestration, agentic workflow design, prompt engineering, LLM evaluation and validation, n8n, Make, Zapier, Microsoft Power Automate'),
  skillLine('Languages & Frameworks','Python, SQL, JavaScript, Flask, React, HTML/CSS'),
  skillLine('Data & Integration','REST API design and integration, webhooks, ETL and data pipelines, vector embeddings, web scraping, SQLite / relational modeling, data validation'),
  skillLine('Platforms & Tooling','Git / GitHub, Railway, Shopify, Klaviyo, QuickBooks, NetSuite, Google Workspace, Microsoft 365, Asana, Slack'),
  skillLine('Practices','Agile / Scrum, requirements analysis, business process mapping, technical documentation, statistical validation (walk-forward, Monte Carlo, Bayesian estimation)'),

  section('CORE COMPETENCIES'),
  body('Workflow Automation  \u2022  AI Agent Orchestration  \u2022  API & Systems Integration  \u2022  Internal Tooling  \u2022  Process Automation  \u2022  Data Pipelines  \u2022  Requirements Analysis  \u2022  Business Process Mapping  \u2022  Production Deployment & Monitoring  \u2022  Technical Documentation',{after:40}),

  section('PROFESSIONAL EXPERIENCE'),

  employer('BEAM LEGACY GROUP','Albany, OR (Remote)','2025 \u2013 Present'),
  title('Founder \u2014 Builder / Principal Implementer'),
  scope('Bootstrapped venture studio. Hands-on across the full stack: specification, implementation, deployment, and operation of production AI systems.'),
  bullet('Architected and shipped an autonomous forecasting and decision-automation platform running live in production \u2014 68 API endpoints, 540 configurable strategy profiles, ensemble modeling, and automated risk controls with real-time hedging.'),
  bullet('Built the statistical validation layer that gates production deployment: walk-forward optimization, 10,000-iteration Monte Carlo simulation, Bayesian estimation, Kelly-criterion sizing, and drawdown and fee-sensitivity analysis \u2014 no strategy reaches live execution without passing it.'),
  bullet('Specify, build, and deploy production-grade platforms as a single operator by directing AI coding agents, covering architecture, implementation, deployment, and iteration on work that would conventionally require a multi-person engineering team.'),
  bullet('Built an AI platform for small-business owners (in beta) integrating search visibility, automated content production, and a business-intelligence layer that unifies data across accounting, point-of-sale, and CRM systems.'),
  bullet('Audited that platform\u2019s scoring engine and remediated 12 defects across three priority tiers \u2014 including double-counted inputs and an image-analysis multiplier capable of depressing a score by over 90% from a single pass \u2014 before customer release.'),
  bullet('Built data pipelines processing hundreds of thousands of records: batch classification and description generation via LLM APIs, vector embedding, dimensionality reduction, and nearest-neighbor computation.'),
  bullet('Reduced inference cost on production workloads through prompt-cache optimization and batch API usage rather than per-request calls.'),

  employer('COMARKCO','Los Angeles, CA','2023 \u2013 2025'),
  title('Chief of Staff  |  Fractional Operations & Finance Lead'),
  scope('Built and automated the operating systems for a portfolio of consumer packaged goods brands \u2014 finance, logistics, compliance, reporting, and data analytics.'),
  bullet('Standardized and automated recurring finance, logistics, and reporting workflows, cutting manual processing time and removing bottlenecks across simultaneous multi-country operations.'),
  bullet('Designed and implemented the operational, financial, and compliance systems that supported revenue growth from $250K to $12M in a single year.'),
  bullet('Built the reporting and data infrastructure used for inventory planning, cash forecasting, and multi-entity financial consolidation.'),
  bullet('Implemented compliance and documentation workflows for intellectual property, tax, product safety certification, and import/export logistics across 20+ countries.'),
  bullet('Integrated finance, e-commerce, and logistics platforms into a single reporting layer, eliminating manual reconciliation between systems.'),

  employer('WORKSITE LABS','Los Angeles, CA','2020 \u2013 2023'),
  title('SVP of Experience & Product'),
  scope('Product owner and systems builder through growth from $0 to $25M in revenue in year one.'),
  bullet('Product Owner for the digital patient booking and reservation platform \u2014 wrote requirements, designed the user experience, and ran sprint planning with engineering through delivery.'),
  bullet('Designed and implemented the data infrastructure capturing customer feedback and engagement telemetry, and built the reporting layer executives used for decision-making.'),
  bullet('Built partner onboarding workflows that shortened implementation timelines and standardized data handoffs across accounts.'),
  bullet('Instrumented CSAT and NPS measurement and used the resulting data to prioritize product and process fixes.'),

  employer('STAIRCASE DIGITAL / WHEELHOUSE STUDIO','Los Angeles, CA','2018 \u2013 2020'),
  title('Director of Operations  |  Technical Product Lead'),
  scope('Led delivery of MyIPO, a Reg A+ digital securities offering platform, and Next Ones, an athlete discovery platform.'),
  bullet('Built analytics and tracking systems for a proprietary advertising methodology, improving campaign targeting and performance.'),
  bullet('Automated ad-creation workflows with real-time analytics feedback, reducing iteration time on campaign production.'),
  bullet('Ran delivery for a cross-functional engineering team using Agile and Scrum, writing requirements and coordinating releases.'),
  bullet('Built financial models and data-driven investor materials supporting successful fundraising.'),

  employer('UNIVERSITY OF CALIFORNIA, IRVINE','Irvine, CA','2013 \u2013 2018'),
  title('Operations Analyst, Civil & Environmental Engineering'),
  bullet('Administered and reported on research grant budgets totaling $3.4M, building the tracking and compliance reporting used across multiple funded projects.'),
  bullet('Automated and streamlined departmental administrative processes, reducing turnaround time and operating cost.'),

  ...EDUCATION,
];

/* ---------------- build ---------------- */
const numbering = { config:[{ reference:'bullets', levels:[{
  level:0, format:LevelFormat.BULLET, text:'\u2022', alignment:AlignmentType.LEFT,
  style:{ paragraph:{ indent:{ left:convertInchesToTwip(0.22), hanging:convertInchesToTwip(0.16) } } },
}]}]};

function build(children, path){
  const doc = new Document({
    numbering,
    sections:[{ properties:{ page:{
      size:{width:12240,height:15840},
      margin:{top:720,right:720,bottom:720,left:720},
    }}, children }],
  });
  return Packer.toBuffer(doc).then(b=>{ fs.writeFileSync(path,b); console.log('wrote',path); });
}

build(LEADER,'/home/claude/James_Beam_Resume_AI_Leadership_2026.docx')
  .then(()=>build(ENGINEER,'/home/claude/James_Beam_Resume_AI_Engineer_2026.docx'));
