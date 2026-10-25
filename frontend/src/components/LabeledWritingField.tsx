import { cloneElement, useId, type ReactElement, type ReactNode } from 'react';
import '../styles/labeled-writing-field.css';

/** Keep the field's label separate from its slash, reference and AI controls. */
export function LabeledWritingField({ label, children }: {
  label: ReactNode;
  children: ReactElement<{ id?: string }>;
}) {
  const generatedId = useId();
  const id = children.props.id || generatedId;
  return <div className="fc-labeled-writing-field">
    <label htmlFor={id}>{label}</label>
    {cloneElement(children, { id })}
  </div>;
}
