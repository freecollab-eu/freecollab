import { AfterViewInit, Component, CUSTOM_ELEMENTS_SCHEMA, ElementRef, Input, OnDestroy, ViewChild } from '@angular/core';
import { defineCustomElements, type CollaborationElement } from '@freecollab/components/elements';
import type { CollaborationClient } from '@freecollab/components';
@Component({ selector: 'app-collaboration', standalone: true, schemas: [CUSTOM_ELEMENTS_SCHEMA], template: '<freecollab-chat #chat></freecollab-chat>' })
export class Collaboration implements AfterViewInit, OnDestroy {
  @Input({required: true}) client!: CollaborationClient;
  @ViewChild('chat') chat!: ElementRef<CollaborationElement>;
  ngAfterViewInit() { defineCustomElements(); this.chat.nativeElement.client = this.client; }
  ngOnDestroy() { this.chat.nativeElement.destroy(); }
}
