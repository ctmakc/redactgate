/**
 * Synthetic notes for the playground. Every name, number and address below is invented
 * for the demo; none belongs to a real person.
 */
export interface Sample {
  id: string;
  label: string;
  who: string;
  text: string;
}

export const SAMPLES: Sample[] = [
  {
    id: "tax",
    label: "tax file note",
    who: "accounting firm",
    text:
      "Client file note, 14 Oct 2026. Client: Daniel Okafor, SIN 046 454 286, OHIP 1234-567-890-AB, " +
      "phone 416-555-0199, email d.okafor@example.ca. Address: 88 Harbour St, Toronto ON M5J 2G2. " +
      "Deposit account at TD: transit 00412, account 5021234. He received a T4 of $84,200 and a T5 of " +
      "$1,350 and asks whether he should open an FHSA this year. Draft a short reply email to him that " +
      "confirms his SIN on file and lists the next two steps.",
  },
  {
    id: "law",
    label: "intake memo",
    who: "law office",
    text:
      "Intake memo. Prospective client Priya Raman (DOB 1988-03-22, phone 647-555-0142, " +
      "priya.raman@example.com) was rear-ended on 2 Sep 2026 at 1200 Bay Street, Toronto. The other " +
      "driver, Marc Tremblay, gave insurer policy number TD-4471-8820. She missed three weeks of work " +
      "at Lakeshore Dental Group. Her SIN is 130 692 544. List the documents we should request from " +
      "her and write the first two paragraphs of a demand letter to the insurer.",
  },
  {
    id: "clinic",
    label: "referral letter",
    who: "family clinic",
    text:
      "Referral. Patient: Mei Chen, DOB 1979-11-05, health card 9876 543 210 XY, " +
      "45 Queen Street, Ottawa ON K1P 5N4, cell 613-555-0187. Reports intermittent chest tightness " +
      "on exertion for six weeks, BP 148/92, LDL 4.1 mmol/L, father had MI at 58. Summarize this for a " +
      "cardiology referral in four bullet points and suggest which tests to order before the visit.",
  },
];
